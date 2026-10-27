import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, status
from pydantic import BaseModel, Field
import requests

load_dotenv()

app = FastAPI(title="LogGuardian AI", version="1.0.0")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

if not GEMINI_API_KEY:
    raise RuntimeError("A variável de ambiente GEMINI_API_KEY não foi configurada.")


class ErrorLogPayload(BaseModel):
    service_name: str = Field(..., description="Nome do microsserviço que gerou o erro")
    environment: str = Field("production", description="Ambiente")
    error_message: str = Field(..., description="Mensagem de erro")


class AnalysisResponse(BaseModel):
    root_cause: str = Field(..., description="Análise de Causa Raiz")
    suggested_fix: str = Field(..., description="Sugestão de correção")
    severity: str = Field(..., description="Severidade")


def analyze_error_with_gemini(service: str, env: str, stack_trace: str) -> AnalysisResponse:
    prompt = f"""
    Você é um Engenheiro SRE sênior. Analise este erro no microsserviço '{service}' em '{env}':
    {stack_trace}
    Retorne estritamente um JSON com as chaves: root_cause, suggested_fix, severity (LOW, MEDIUM, HIGH, CRITICAL).
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=15)
        response.raise_for_status()
        data = response.json()
        text_content = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed_data = json.loads(text_content)
        return AnalysisResponse(**parsed_data)
    except (requests.RequestException, KeyError, json.JSONDecodeError, IndexError) as e:
        return AnalysisResponse(
            root_cause=f"Falha ao gerar análise via IA: {e!s}",
            suggested_fix="Verifique os logs brutos.",
            severity="HIGH"
        )


def send_discord_alert(service: str, env: str, analysis: AnalysisResponse):
    if not DISCORD_WEBHOOK_URL:
        return
    colors = {"LOW": 3066993, "MEDIUM": 16776960, "HIGH": 15105570, "CRITICAL": 15158332}
    payload = {
        "username": "LogGuardian AI 🛡️",
        "embeds": [{
            "title": f"🚨 Alerta de Erro: {service} ({env})",
            "color": colors.get(analysis.severity.upper(), 15158332),
            "fields": [
                {"name": "🔍 Causa Raiz (RCA)", "value": analysis.root_cause, "inline": False},
                {"name": "🛠️ Sugestão de Correção", "value": f"```markdown\n{analysis.suggested_fix}\n```", "inline": False},
                {"name": "⚡ Severidade", "value": f"`{analysis.severity}`", "inline": True}
            ],
            "footer": {"text": "LogGuardian AI — SRE"}
        }]
    }
    requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)


@app.post("/log-error", status_code=status.HTTP_201_CREATED)
def receive_error_log(payload: ErrorLogPayload):
    analysis = analyze_error_with_gemini(payload.service_name, payload.environment, payload.error_message)
    send_discord_alert(payload.service_name, payload.environment, analysis)
    return {"status": "success", "analysis": analysis}