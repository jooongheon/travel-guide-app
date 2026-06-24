const questionInput = document.getElementById("questionInput");
const askButton = document.getElementById("askButton");
const answerText = document.getElementById("answerText");
const loading = document.getElementById("loading");
const speakButton = document.getElementById("speakButton");

askButton.addEventListener("click", async () => {
  const question = questionInput.value.trim();

  if (!question) {
    alert("질문을 입력해주세요.");
    return;
  }

  loading.classList.remove("hidden");
  answerText.textContent = "";

  try {
    const response = await fetch("/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        question: question,
        refresh: false
      })
    });

    const data = await response.json();

    answerText.textContent = data.answer;

    console.log("응답:", data);
  } catch (error) {
    answerText.textContent = "오류가 발생했습니다. 서버를 확인해주세요.";
    console.error(error);
  } finally {
    loading.classList.add("hidden");
  }
});

speakButton.addEventListener("click", () => {
  const text = answerText.textContent;

  if (!text || text === "아직 질문이 없습니다.") {
    alert("먼저 질문을 해주세요.");
    return;
  }

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ko-KR";
  utterance.rate = 1.0;

  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
});