import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


# =========================
# 기본 설정
# =========================

BASE_DIR = Path(__file__).resolve().parent
PLACES_FILE = BASE_DIR / "places.json"
CACHE_FILE = BASE_DIR / "cache.json"

load_dotenv()

app = FastAPI()

STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


# =========================
# 요청 데이터 형태
# =========================

class AskRequest(BaseModel):
    question: str
    refresh: bool = False
    # refresh가 False면 캐시 사용
    # refresh가 True면 캐시 무시하고 OpenAI API 새로 호출


# =========================
# places.json 읽기
# =========================

def load_places():
    with open(PLACES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def find_place(question: str):
    places = load_places()

    for place in places:
        if place["name"] in question:
            return place

    return None


# =========================
# 질문 의도 분류
# =========================

def classify_intent(question: str):
    """
    사용자의 질문을 간단한 키워드 기반으로 분류합니다.
    나중에는 이 부분도 LLM이나 임베딩 검색으로 바꿀 수 있습니다.
    """

    q = question.replace(" ", "")

    if any(word in q for word in ["역사", "배경", "시대", "대한제국", "조선", "고종"]):
        return "역사배경"

    if any(word in q for word in ["일화", "이야기", "사건", "재밌는", "비하인드"]):
        return "관련일화"

    if any(word in q for word in ["볼거리", "관람", "포인트", "어디를봐", "뭘봐", "추천"]):
        return "관람포인트"

    if any(word in q for word in ["석조전", "중화전", "대한문", "돌담길"]):
        return "세부장소설명"

    return "기본설명"


# =========================
# cache.json 관련 함수
# =========================

def load_cache():
    if not CACHE_FILE.exists():
        return {}

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def make_cache_key(place_name: str, intent: str):
    return f"{place_name}:{intent}"


# =========================
# OpenAI usage 출력용
# =========================

def usage_to_dict(usage):
    if usage is None:
        return None

    if hasattr(usage, "model_dump"):
        return usage.model_dump()

    return str(usage)


# =========================
# 프롬프트 생성
# =========================

def build_prompt(place: dict, question: str, intent: str):
    name = place.get("name", "")
    category = place.get("category", "")
    location = place.get("location", "")
    summary = place.get("summary", "")
    background = place.get("background", "")
    history = place.get("history", [])
    episodes = place.get("episodes", [])
    view_points = place.get("view_points", [])
    keywords = place.get("keywords", [])

    if intent == "기본설명":
        intent_instruction = """
사용자는 이 장소가 어떤 곳인지 전체적으로 알고 싶어 한다.
장소의 핵심 특징, 역사적 배경, 대표 관람 포인트를 균형 있게 설명해라.
"""

    elif intent == "역사배경":
        intent_instruction = """
사용자는 이 장소의 역사적 배경을 알고 싶어 한다.
이 장소가 어떤 시대와 연결되는지, 어떤 인물이나 사건과 관련 있는지 중심으로 설명해라.
"""

    elif intent == "관련일화":
        intent_instruction = """
사용자는 이 장소와 관련된 이야기나 일화를 알고 싶어 한다.
제공된 장소 정보 안에서 흥미로운 배경과 이야기를 자연스럽게 풀어 설명해라.
확실하지 않은 일화는 지어내지 마라.
"""

    elif intent == "관람포인트":
        intent_instruction = """
사용자는 이 장소에서 무엇을 보면 좋은지 알고 싶어 한다.
현장에서 관람할 때 눈여겨볼 건물, 동선, 분위기, 관찰 포인트를 중심으로 설명해라.
"""

    elif intent == "세부장소설명":
        intent_instruction = """
사용자는 장소 안의 특정 건물이나 세부 공간에 대해 알고 싶어 한다.
질문에 나온 세부 장소를 중심으로 설명하되, 전체 장소와의 관계도 함께 설명해라.
"""

    else:
        intent_instruction = """
사용자의 질문에 맞게 장소 정보를 바탕으로 자연스럽게 설명해라.
"""

    prompt = f"""
너는 한국 역사 여행지를 설명하는 전문 여행 가이드다.
사용자는 지금 실제로 '{name}'에 방문한 관광객이라고 가정한다.

사용자 질문 의도:
{intent}

의도별 답변 방향:
{intent_instruction}

답변 목표:
사용자에게 이 장소의 단순 정보가 아니라, 장소의 배경, 역사적 의미, 관련 인물, 관련 일화, 관람 포인트를 자연스럽게 설명한다.

말투:
- 반드시 존댓말을 사용한다.
- 백과사전처럼 딱딱하게 나열하지 않는다.
- 실제 현장에서 가이드가 관광객에게 설명하듯이 말한다.
- 역사 내용을 쉽게 풀어서 설명한다.
- 너무 과장하거나 감성적으로만 말하지 않는다.
- 문장은 자연스럽게 이어지게 작성한다.

답변 규칙:
- 제목이나 번호 목록보다는 실제 가이드가 말하는 흐름으로 설명한다.
- 답변은 1~2분 정도 말로 들을 수 있는 분량으로 작성한다.
- 아래 장소 정보에 없는 아주 구체적인 사실은 함부로 지어내지 않는다.
- 확실하지 않은 내용은 단정하지 않는다.
- 마지막에는 이 장소를 어떤 관점으로 보면 좋은지 한 문장으로 정리한다.

장소 정보:
이름: {name}
분류: {category}
위치: {location}
요약: {summary}
배경: {background}
역사: {history}
관련 일화: {episodes}
관람 포인트: {view_points}
키워드: {keywords}

사용자 질문:
{question}
"""

    return prompt


# =========================
# 기본 페이지
# =========================

@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


# =========================
# 질문 API
# =========================

@app.post("/ask")
def ask_guide(req: AskRequest):
    place = find_place(req.question)

    if place is None:
        return {
            "answer": "아직 제가 알고 있는 여행지 목록에 없는 장소입니다. 현재는 places.json에 등록된 장소만 안내할 수 있습니다.",
            "cached": False
        }

    place_name = place.get("name", "")
    intent = classify_intent(req.question)

    cache = load_cache()
    cache_key = make_cache_key(place_name, intent)

    # refresh가 False이고 캐시에 답변이 있으면 OpenAI API를 호출하지 않음
    if not req.refresh and cache_key in cache:
        return {
            "place": place_name,
            "intent": intent,
            "answer": cache[cache_key],
            "cached": True,
            "usage": None
        }

    prompt = build_prompt(place, req.question, intent)

    response = client.responses.create(
        model="gpt-5-nano",
        input=prompt
    )

    answer = response.output_text

    # 새로 생성한 답변을 캐시에 저장
    cache[cache_key] = answer
    save_cache(cache)

    return {
        "place": place_name,
        "intent": intent,
        "answer": answer,
        "cached": False,
        "usage": usage_to_dict(response.usage)
    }