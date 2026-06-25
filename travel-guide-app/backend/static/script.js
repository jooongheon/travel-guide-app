const questionInput = document.getElementById("questionInput");
const askButton = document.getElementById("askButton");
const answerText = document.getElementById("answerText");
const loading = document.getElementById("loading");
const speakButton = document.getElementById("speakButton");

let eventSource = null;
let currentUtterance = null;
let isSpeaking = false;

const speakButtonDefaultText = speakButton.textContent.trim();
const speakButtonStopText = "■ 정지";

function resetSpeakButton() {
  isSpeaking = false;
  currentUtterance = null;
  speakButton.textContent = speakButtonDefaultText;
  speakButton.classList.remove("is-speaking");
}

function stopSpeaking() {
  speechSynthesis.cancel();
  resetSpeakButton();
}

// 현재 대화 중인 장소를 기억하는 변수
let currentPlace = null;

askButton.addEventListener("click", () => {
  const question = questionInput.value.trim();

  if (!question) {
    alert("질문을 입력해주세요.");
    return;
  }

  // 기존 SSE 연결이 남아있으면 닫기
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }

  stopSpeaking();

  loading.classList.remove("hidden");
  loading.textContent = "가이드 설명을 준비하고 있습니다...";
  answerText.textContent = "";
  askButton.disabled = true;

  // 현재 장소가 있으면 서버에 같이 보냄
  let url = `/ask/stream?question=${encodeURIComponent(question)}&refresh=false`;

  if (currentPlace) {
    url += `&current_place=${encodeURIComponent(currentPlace)}`;
  }

  console.log("요청 URL:", url);
  console.log("현재 기억 중인 장소:", currentPlace);

  eventSource = new EventSource(url);

  eventSource.onopen = () => {
    console.log("SSE 연결 성공");
  };

  eventSource.addEventListener("meta", (event) => {
    console.log("meta 이벤트 원본:", event.data);

    try {
      const data = JSON.parse(event.data);
      console.log("메타 정보:", data);

      // 서버가 인식한 장소를 currentPlace에 저장
      if (data.place) {
        currentPlace = data.place;
        console.log("현재 장소 저장:", currentPlace);
      }

      if (data.cached) {
        loading.textContent = "저장된 답변을 불러오고 있습니다...";
      } else {
        loading.textContent = "AI 가이드가 설명을 생성하고 있습니다...";
      }
    } catch (error) {
      console.error("meta JSON 파싱 오류:", error);
    }
  });

  function appendAnswer(event) {
    console.log("받은 SSE 데이터:", event.data);

    if (!event.data || event.data === "[DONE]") {
      return;
    }

    try {
      const data = JSON.parse(event.data);

      const text =
        data.text ||
        data.answer ||
        data.content ||
        data.choices?.[0]?.delta?.content ||
        "";

      console.log("화면에 추가할 text:", text);

      if (text) {
        answerText.textContent += text;
      }
    } catch (error) {
      console.error("JSON 파싱 오류:", error);
      console.log("원본 데이터:", event.data);
    }
  }

  // 서버가 event: delta 형식으로 보낼 때
  eventSource.addEventListener("delta", appendAnswer);

  // 서버가 event 이름 없이 data만 보낼 때 대비
  eventSource.onmessage = appendAnswer;

  eventSource.addEventListener("done", () => {
    console.log("SSE 완료");

    loading.classList.add("hidden");
    askButton.disabled = false;

    eventSource.close();
    eventSource = null;
  });

  eventSource.addEventListener("server_error", (event) => {
    console.error("server_error:", event.data);

    try {
      const data = JSON.parse(event.data);
      answerText.textContent += data.text || data.error || "서버 오류가 발생했습니다.";
    } catch (error) {
      answerText.textContent += "서버 오류가 발생했습니다.";
    }

    loading.classList.add("hidden");
    askButton.disabled = false;

    eventSource.close();
    eventSource = null;
  });

  eventSource.onerror = (error) => {
    console.error("SSE 연결 오류:", error);

    if (eventSource) {
      console.log("readyState:", eventSource.readyState);
    }

    loading.classList.add("hidden");
    askButton.disabled = false;

    if (!answerText.textContent.trim()) {
      answerText.textContent = "연결 중 오류가 발생했습니다.";
    }

    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  };
});

speakButton.addEventListener("click", () => {
  if (isSpeaking) {
    stopSpeaking();
    return;
  }

  const text = answerText.textContent.trim();

  if (!text || text === "아직 질문이 없습니다.") {
    alert("먼저 가이드 설명을 생성해주세요.");
    return;
  }

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ko-KR";
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.onend = resetSpeakButton;
  utterance.onerror = resetSpeakButton;

  speechSynthesis.cancel();
  currentUtterance = utterance;
  isSpeaking = true;
  speakButton.textContent = speakButtonStopText;
  speakButton.classList.add("is-speaking");
  speechSynthesis.speak(utterance);
});
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/service-worker.js")
      .then((registration) => {
        console.log("Service Worker 등록 성공:", registration.scope);
      })
      .catch((error) => {
        console.error("Service Worker 등록 실패:", error);
      });
  });
}
