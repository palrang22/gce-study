"""AI 해설 생성 (google-genai). 설정은 .env 참고.

GEMINI_USE_VERTEX=true 면 Vertex AI + gcloud ADC 인증
(`gcloud auth application-default login`), 아니면 GEMINI_API_KEY 사용.
"""
import os
from functools import lru_cache

import google.auth
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

TIMEOUT_MS = 90_000

EXAM_NAMES = {
    "ACE": "ACE(Associate Cloud Engineer)",
    "PCA": "PCA(Professional Cloud Architect)",
}

PROMPT_TEMPLATE = """너는 Google Cloud {exam_name} 시험 튜터야.
클라우드 초심자에게 설명하듯 한국어로 답해. GCP 서비스명과 명령어는 영어 그대로 써.

[문제]
{question}

[보기]
{options}

[정답] {answer}
[내가 고른 답] {selected}
[원본 해설] {explanation}

아래 순서로 마크다운으로 답해:
1. 정답이 맞는 이유 (핵심 한 문장 + 보충 설명)
2. 각 오답 보기가 틀린 이유 (보기별 한두 줄)
3. 내가 고른 답이 오답이면, 왜 헷갈리기 쉬운지
4. 이 문제와 관련된 GCP 개념 정리 (3줄 이내)
5. 시험에서 비슷한 문제를 빨리 푸는 키워드 팁
원본 해설과 네 판단이 다르면 그 사실을 명시해."""


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f".env 에 {name} 가 비어 있음")
    return value


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    http_options = types.HttpOptions(timeout=TIMEOUT_MS)
    if os.environ.get("GEMINI_USE_VERTEX", "").strip().lower() == "true":
        project = _env("GOOGLE_CLOUD_PROJECT")
        # ADC 파일에 다른 quota project 가 잡혀 있어도 이 프로젝트로 할당량/비용이 잡히게 한다
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
            quota_project_id=project,
        )
        return genai.Client(
            vertexai=True,
            project=project,
            location=_env("GOOGLE_CLOUD_LOCATION"),
            credentials=credentials,
            http_options=http_options,
        )
    return genai.Client(api_key=_env("GEMINI_API_KEY"), http_options=http_options)


def build_prompt(question: dict, selected: list[str]) -> str:
    explanation = question.get("explanation") or ""
    if explanation.startswith("http"):
        # 이 문제집은 해설 대신 examtopics 토론 링크만 있다
        explanation = f"(해설 문장 없음, 토론 링크만 있음: {explanation})"
    exam = question.get("exam", "ACE")
    return PROMPT_TEMPLATE.format(
        exam_name=EXAM_NAMES.get(exam, exam),
        question=question["question"],
        options="\n".join(f"{k}. {v}" for k, v in question["options"].items()),
        answer=", ".join(question["answer"]),
        selected=", ".join(selected) if selected else "(선택 안 함)",
        explanation=explanation or "(없음)",
    )


def generate_explanation(question: dict, selected: list[str]) -> tuple[str, str]:
    """(마크다운 해설, 모델명) 반환. 실패하면 예외를 그대로 올린다."""
    model = _env("GEMINI_MODEL")
    response = _client().models.generate_content(
        model=model,
        contents=build_prompt(question, selected),
        config=types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("모델이 빈 응답을 반환함")
    return text, model


def generate_followup(
    question: dict,
    selected: list[str],
    base_explanation: str,
    prior_messages: list[dict],
    new_question: str,
) -> tuple[str, str]:
    """AI 해설에 이어서 질문 하나를 더 묻는다. (답변, 모델명) 반환.

    prior_messages: [{"role": "user"|"model", "content": "..."}] 이 질문 이전까지의 대화, 오래된 순.
    매 호출마다 처음 해설부터 전체 맥락을 다시 보내는 stateless 방식 (서버에 세션을 들고 있지 않음).
    """
    model = _env("GEMINI_MODEL")
    contents = [
        types.Content(role="user", parts=[types.Part(text=build_prompt(question, selected))]),
        types.Content(role="model", parts=[types.Part(text=base_explanation)]),
    ]
    for m in prior_messages:
        role = "model" if m["role"] == "model" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=new_question)]))

    response = _client().models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("모델이 빈 응답을 반환함")
    return text, model
