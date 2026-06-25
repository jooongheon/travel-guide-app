import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse


# =========================
# 기본 설정
# =========================

BASE_DIR = Path(__file__).resolve().parent
PLACES_FILE = BASE_DIR / "places.json"
CACHE_FILE = BASE_DIR / "cache.json"
STATIC_DIR = BASE_DIR / "static"

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5-nano")
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1100"))
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "minimal")
TEXT_VERBOSITY = os.getenv("OPENAI_TEXT_VERBOSITY", "medium")


# =========================
# 요청 데이터 형태
# =========================

class AskRequest(BaseModel):
    question: str
    refresh: bool = False
    current_place: Optional[str] = None


# =========================
# 문자열 정규화
# =========================

def normalize_text(text: str):
    return str(text).replace(" ", "").lower()


def normalize_question(question: str):
    return question.strip().replace(" ", "").lower()


# =========================
# JSON 파일 읽기/쓰기
# =========================

def read_json_file(file_path: Path, default):
    if not file_path.exists():
        return default

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def write_json_file(file_path: Path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cache():
    return read_json_file(CACHE_FILE, {})


def save_cache(cache):
    write_json_file(CACHE_FILE, cache)


# =========================
# 표준 장소 데이터 변환
# =========================

def normalize_view_points(raw_view_points):
    result = []

    if not isinstance(raw_view_points, list):
        return result

    for point in raw_view_points:
        if isinstance(point, dict):
            name = str(point.get("name", "")).strip()

            if not name:
                continue

            result.append({
                "name": name,
                "type": str(point.get("type", "")).strip(),
                "summary": str(point.get("summary", "")).strip()
            })

        else:
            name = str(point).strip()

            if not name:
                continue

            result.append({
                "name": name,
                "type": "",
                "summary": ""
            })

    return result


def normalize_string_list(value):
    if not isinstance(value, list):
        return []

    result = []

    for item in value:
        text = str(item).strip()

        if text:
            result.append(text)

    return result


def normalize_place(raw_place: dict, source: str = "local"):
    """
    places.json / 지도 API / TourAPI / DB 등에서 온 장소 데이터를
    우리 앱에서 쓰는 표준 Place 형태로 바꿉니다.
    """

    name = str(raw_place.get("name", "")).strip()

    return {
        "id": str(raw_place.get("id", name)).strip(),
        "source": source,

        "name": name,
        "aliases": normalize_string_list(raw_place.get("aliases", [])),

        "category": str(raw_place.get("category", "")).strip(),
        "location": str(raw_place.get("location", raw_place.get("address", ""))).strip(),

        "latitude": raw_place.get("latitude"),
        "longitude": raw_place.get("longitude"),

        "summary": str(raw_place.get("summary", "")).strip(),
        "background": str(raw_place.get("background", "")).strip(),

        "history": raw_place.get("history", []) if isinstance(raw_place.get("history", []), list) else [],
        "episodes": raw_place.get("episodes", []) if isinstance(raw_place.get("episodes", []), list) else [],

        "view_points": normalize_view_points(raw_place.get("view_points", [])),

        # keywords는 답변 참고용으로 사용
        "keywords": normalize_string_list(raw_place.get("keywords", []))
    }


# =========================
# 장소 데이터 저장소
# =========================

class LocalPlaceRepository:
    """
    지금은 places.json을 읽는 임시 저장소입니다.
    나중에는 이 클래스를 지도 API / DB 저장소로 교체하면 됩니다.
    """

    def load_all(self):
        raw_places = read_json_file(PLACES_FILE, [])
        return [normalize_place(place, source="local") for place in raw_places]

    def find_by_name(self, place_name: str):
        target = normalize_text(place_name)

        for place in self.load_all():
            if normalize_text(place["name"]) == target:
                return place

            for alias in place.get("aliases", []):
                if normalize_text(alias) == target:
                    return place

        return None

    def find_by_question(self, question: str):
        q = normalize_text(question)
        places = self.load_all()

        # 1. 장소명으로 찾기
        for place in places:
            name_key = normalize_text(place["name"])

            if name_key and name_key in q:
                return place

        # 2. 별칭으로 찾기
        for place in places:
            for alias in place.get("aliases", []):
                alias_key = normalize_text(alias)

                if alias_key and alias_key in q:
                    return place

        # 3. 세부 관람 포인트로 찾기
        # 예: "석조전은 뭐야?" → 덕수궁
        # 예: "경회루는 뭐야?" → 경복궁
        for place in places:
            for point in place.get("view_points", []):
                point_key = normalize_text(point.get("name", ""))

                if point_key and point_key in q:
                    return place

        return None


place_repository = LocalPlaceRepository()


def find_place(question: str, current_place: Optional[str] = None):
    """
    장소 검색 우선순위:
    1. 질문 안에서 장소명 / 별칭 / 세부 장소명 검색
    2. 못 찾으면 current_place 사용
    """

    place = place_repository.find_by_question(question)

    if place:
        return place

    if current_place:
        return place_repository.find_by_name(current_place)

    return None


# =========================
# 장소 데이터 포맷팅
# =========================

def format_view_points_for_prompt(place: dict):
    points = place.get("view_points", [])

    if not points:
        return "제공된 관람 포인트 정보 없음"

    lines = []

    for point in points:
        name = point.get("name", "")
        point_type = point.get("type", "")
        summary = point.get("summary", "")

        line = f"- {name}"

        if point_type:
            line += f" ({point_type})"

        if summary:
            line += f": {summary}"

        lines.append(line)

    return "\n".join(lines)


def format_list_for_prompt(items):
    if not items:
        return "제공된 정보 없음"

    if isinstance(items, list):
        result = []

        for item in items:
            if isinstance(item, dict):
                result.append(f"- {json.dumps(item, ensure_ascii=False)}")
            else:
                result.append(f"- {item}")

        return "\n".join(result)

    return str(items)


# =========================
# 질문 의도 분류
# =========================

def classify_intent(question: str, place: Optional[dict] = None):
    """
    질문 의도를 코드가 세세하게 분류하지 않습니다.
    장소 찾기와 데이터 제공은 코드가 담당하고,
    질문 이해와 답변 방식은 OpenAI가 담당합니다.
    """
    return "자유질문"


# =========================
# 캐시 키 생성
# =========================

def place_cache_id(place: dict):
    source = place.get("source", "unknown")
    place_id = place.get("id") or place.get("name", "unknown_place")

    return f"{source}:{place_id}"


def make_cache_key(place: dict, intent: str, question: str):
    """
    같은 장소라도 질문이 다르면 다른 캐시를 사용합니다.
    """
    base = place_cache_id(place)
    q = normalize_question(question)

    return f"{base}:question:{q}"


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
# SSE 이벤트 생성 함수
# =========================

def sse_event(event_name: str, data: dict):
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# =========================
# 프롬프트 생성
# =========================

def build_prompt(place: dict, question: str, intent: str):
    name = place.get("name", "")
    category = place.get("category", "")
    location = place.get("location", "")
    summary = place.get("summary", "")
    background = place.get("background", "")

    history_text = format_list_for_prompt(place.get("history", []))
    episodes_text = format_list_for_prompt(place.get("episodes", []))
    view_points_text = format_view_points_for_prompt(place)
    keywords = place.get("keywords", [])

    prompt = f"""
너는 한국 여행지를 설명하는 전문 AI 여행 가이드다.
사용자는 지금 '{name}'에 대해 질문하고 있다.

너의 역할:
- 단순 정보 검색 결과를 읽어주는 사람이 아니다.
- 실제 현장에서 관광객 옆에 서서 설명해주는 가이드처럼 말한다.
- 사용자의 질문에 정확히 답하되, 그 장소를 더 흥미롭게 볼 수 있도록 배경, 역사, 일화, 관람 포인트를 자연스럽게 덧붙인다.

가장 중요한 답변 원칙:
- 첫 문장은 반드시 사용자 질문에 대한 직접 답변이어야 한다.
- 질문이 "있어?", "있나요?", "볼 수 있어?", "가능해?" 같은 형태라면 먼저 "네, 있습니다.", "네, 볼 수 있습니다.", 또는 "제공된 정보만으로는 확실히 확인하기 어렵습니다."처럼 답한다.
- 직접 답변 후에는 너무 끊기듯 끝내지 말고, 그 장소와 관련된 역사적 배경, 흥미로운 이야기, 관람 포인트를 자연스럽게 이어서 설명한다.
- 사용자가 묻지 않았더라도, 장소 정보에 있는 역사·일화·관람 포인트 중 하나 이상을 곁들여 설명한다.
- 단, 질문과 전혀 상관없는 주변 관광지 추천이나 코스 추천으로 넘어가지 않는다.
- 제공된 장소 정보에 근거해서만 답한다.
- 확실하지 않은 사실은 지어내지 않는다.
- 모르는 내용은 모른다고 말하되, 장소 정보 안에서 확인 가능한 관련 포인트를 안내한다.

말투:
- 반드시 존댓말을 사용한다.
- 백과사전처럼 딱딱하게 쓰지 않는다.
- "이곳을 보실 때는...", "여기서 재미있는 점은...", "잠깐 배경을 알고 보면..." 같은 현장 가이드식 표현을 자연스럽게 사용한다.
- 너무 과장하거나 광고 문구처럼 쓰지 않는다.
- 친절하지만 가볍지 않게 설명한다.
- 문장 사이를 자연스럽게 이어서 실제 사람이 말하는 것처럼 작성한다.

답변 길이:
- 기본적으로 60~90초 정도 말로 들을 수 있는 분량으로 작성한다.
- 너무 짧게 한두 문장으로 끝내지 않는다.
- 보통 4~6문단 정도로 작성한다.
- 사용자가 아주 간단한 확인 질문을 해도, 직접 답변 뒤에 관련 배경이나 관람 팁을 짧게 덧붙인다.

답변 구성:
1. 질문에 대한 직접 답변
2. 그 답변과 연결되는 장소 내부 요소 설명
3. 역사적 배경이나 관련 일화
4. 현장에서 무엇을 보면 좋은지 관람 포인트
5. 마지막에 이 장소를 어떤 관점으로 보면 좋은지 자연스럽게 정리

주의할 점:
- 사용자가 경복궁 안의 연못을 물었는데 광화문, 북촌, 덕수궁 같은 주변 장소를 추천하지 마라.
- 사용자가 특정 질문을 했는데 장소 전체 소개를 처음부터 반복하지 마라.
- "제공된 정보만으로는..."이라는 말을 남발하지 마라. 장소 정보에 단서가 있으면 그 단서를 바탕으로 설명해라.
- 장소 정보에 없는 구체적 연도, 사건, 인물 관계를 새로 만들어내지 마라.
- 답변을 너무 건조하게 끝내지 말고, 실제로 그 장소를 보고 싶어지게 설명해라.

좋은 답변 예시 1:
사용자 질문: "경복궁 안에 연못도 있어?"
답변 방향:
"네, 경복궁 안에는 연못을 볼 수 있는 공간이 있습니다. 대표적으로 경회루는 연못 위에 세워진 누각이고, 향원정도 연못과 함께 조용한 분위기를 느낄 수 있는 공간입니다. 그냥 물가 풍경으로만 보지 마시고, 왕실의 연회와 휴식 공간이라는 관점에서 보면 훨씬 흥미롭습니다..."

좋은 답변 예시 2:
사용자 질문: "덕수궁 설명해줘"
답변 방향:
"덕수궁은 조선의 궁궐이면서 동시에 대한제국의 분위기가 강하게 남아 있는 장소입니다. 이곳의 재미있는 점은 전통 궁궐 건물과 서양식 건축물이 함께 있다는 점입니다. 그래서 덕수궁을 보실 때는 단순히 오래된 궁궐로 보기보다, 조선에서 근대로 넘어가던 변화의 현장으로 보시면 좋습니다..."

장소 정보:
이름: {name}
분류: {category}
위치: {location}
요약: {summary}
배경: {background}

역사:
{history_text}

관련 일화:
{episodes_text}

관람 포인트:
{view_points_text}

키워드:
{keywords}

사용자 질문:
{question}

위 장소 정보만 근거로 사용자의 질문에 직접 답하고, 실제 여행 가이드처럼 자연스럽고 흥미롭게 설명해라.
"""

    return prompt


# =========================
# 기본 페이지 / PWA 파일
# =========================

@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.json")
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.json",
        media_type="application/manifest+json"
    )


@app.get("/service-worker.js")
def service_worker():
    return FileResponse(
        STATIC_DIR / "service-worker.js",
        media_type="application/javascript"
    )


# =========================
# 일반 질문 API
# =========================

@app.post("/ask")
def ask_guide(req: AskRequest):
    place = find_place(req.question, req.current_place)

    if place is None:
        return {
            "answer": "아직 어떤 장소에 대한 질문인지 알 수 없습니다. 먼저 '덕수궁에 대해서 설명해줘'처럼 장소명을 포함해서 질문해주세요.",
            "cached": False
        }

    place_name = place.get("name", "")
    intent = classify_intent(req.question, place)

    cache = load_cache()
    cache_key = make_cache_key(place, intent, req.question)

    if not req.refresh and cache_key in cache:
        return {
            "place": place_name,
            "intent": intent,
            "cache_key": cache_key,
            "answer": cache[cache_key],
            "cached": True,
            "usage": None
        }

    prompt = build_prompt(place, req.question, intent)

    response = client.responses.create(
        model=MODEL_NAME,
        input=prompt,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        reasoning={
            "effort": REASONING_EFFORT
        },
        text={
            "verbosity": TEXT_VERBOSITY
        }
    )

    if getattr(response, "status", None) == "incomplete":
        answer = response.output_text or "답변 생성이 중간에 끊겼습니다. 질문을 조금 짧게 하거나 max_output_tokens 값을 늘려보세요."
    else:
        answer = response.output_text

    if answer and "답변 생성이 중간에 끊겼습니다" not in answer:
        cache[cache_key] = answer
        save_cache(cache)

    return {
        "place": place_name,
        "intent": intent,
        "cache_key": cache_key,
        "answer": answer,
        "cached": False,
        "usage": usage_to_dict(response.usage)
    }


# =========================
# 스트리밍 질문 API
# =========================

@app.get("/ask/stream")
def ask_guide_stream(
    question: str,
    refresh: bool = False,
    current_place: Optional[str] = None
):
    def generate():
        place = find_place(question, current_place)

        if place is None:
            yield sse_event("delta", {
                "text": "아직 어떤 장소에 대한 질문인지 알 수 없습니다. 먼저 '덕수궁에 대해서 설명해줘'처럼 장소명을 포함해서 질문해주세요."
            })
            yield sse_event("done", {})
            return

        place_name = place.get("name", "")
        intent = classify_intent(question, place)

        cache = load_cache()
        cache_key = make_cache_key(place, intent, question)

        if not refresh and cache_key in cache:
            yield sse_event("meta", {
                "place": place_name,
                "intent": intent,
                "cache_key": cache_key,
                "cached": True
            })

            yield sse_event("delta", {
                "text": cache[cache_key]
            })

            yield sse_event("done", {})
            return

        prompt = build_prompt(place, question, intent)

        yield sse_event("meta", {
            "place": place_name,
            "intent": intent,
            "cache_key": cache_key,
            "cached": False
        })

        full_answer = ""

        try:
            stream = client.responses.create(
                model=MODEL_NAME,
                input=prompt,
                stream=True,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                reasoning={
                    "effort": REASONING_EFFORT
                },
                text={
                    "verbosity": TEXT_VERBOSITY
                }
            )

            for event in stream:
                event_type = getattr(event, "type", None)

                print("OPENAI EVENT:", event_type)

                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "")

                    if delta:
                        full_answer += delta

                        yield sse_event("delta", {
                            "text": delta
                        })

                elif event_type == "response.completed":
                    response = getattr(event, "response", None)

                    if response is not None:
                        final_text = getattr(response, "output_text", "")

                        if final_text and not full_answer:
                            full_answer = final_text

                            yield sse_event("delta", {
                                "text": final_text
                            })

                elif event_type == "response.incomplete":
                    if full_answer:
                        yield sse_event("delta", {
                            "text": "\n\n답변이 일부만 생성되었습니다. 필요하면 다시 질문해주세요."
                        })
                        yield sse_event("done", {})
                    else:
                        yield sse_event("server_error", {
                            "text": "답변 생성이 토큰 제한 때문에 시작되기 전에 끊겼습니다. MAX_OUTPUT_TOKENS 값을 늘리거나 프롬프트를 더 짧게 줄여보세요."
                        })
                    return

                elif event_type == "error":
                    error_message = getattr(event, "message", "OpenAI 스트리밍 중 오류가 발생했습니다.")
                    yield sse_event("server_error", {
                        "text": error_message
                    })
                    return

            if not full_answer:
                yield sse_event("server_error", {
                    "text": "OpenAI 응답은 완료되었지만 화면에 출력할 텍스트가 오지 않았습니다. 서버 터미널의 OPENAI EVENT 로그를 확인해주세요."
                })
                return

            cache[cache_key] = full_answer
            save_cache(cache)

            yield sse_event("done", {})

        except Exception as e:
            yield sse_event("server_error", {
                "text": f"오류가 발생했습니다: {str(e)}"
            })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )