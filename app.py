import asyncio
import base64
import json
import os
import time
import traceback
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict
from enum import Enum
from dataclasses import dataclass

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="AI English Dojo", layout="wide")

# ============================================================
# Secrets / Config (Cloud-first: st.secrets, fallback to env)
# ============================================================
def _get_secret(name: str, default: str = "") -> str:
    """Streamlit Cloud의 st.secrets를 우선 사용하고, 없으면 환경변수, 그것도 없으면 default."""
    try:
        # st.secrets는 키가 없으면 KeyError를 던지므로 안전하게 감싼다.
        if name in st.secrets:
            val = st.secrets[name]
            if val is not None and str(val).strip() != "":
                return str(val)
    except Exception:
        # 로컬에서 secrets.toml이 없으면 st.secrets 자체가 예외를 낼 수 있음.
        pass
    return os.environ.get(name, default) or default

API_KEY = _get_secret("OPENAI_API_KEY", "")
APP_PASSWORD = _get_secret("APP_PASSWORD", "")
ENV_REALTIME_MODEL = _get_secret("OPENAI_REALTIME_MODEL", "")
ENV_REALTIME_VOICE = _get_secret("OPENAI_REALTIME_VOICE", "")
BUILD_ID = "2026-05-23-cloud-v1.0"


def require_password_gate():
    """단순 비밀번호 게이트.
    - APP_PASSWORD가 비어있으면(=로컬 개발 모드) 통과시킨다.
    - 이미 인증되었으면 통과.
    - 아니면 입력창을 띄우고 st.stop()으로 뒤쪽 코드를 막는다.
    """
    if not APP_PASSWORD:
        return  # 로컬 모드 / 비밀번호 미설정 → 게이트 비활성
    if st.session_state.get("_authenticated") is True:
        return

    st.markdown("## 🔒 AI English Dojo")
    st.caption("비공개 영어 학습 앱입니다. 접속 비밀번호를 입력하세요.")
    pw = st.text_input("Password", type="password", key="_pw_input")
    if st.button("Enter"):
        if pw == APP_PASSWORD:
            st.session_state["_authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()

if 'user_settings' not in st.session_state:
    st.session_state.user_settings = {}

# ===============================================
# TARGET SPEED CONTROL TOWER
# st.session_state.target_speed를 전역 컨트롤 타워로 사용
# ===============================================

def initialize_target_speed(level_value: str) -> float:
    """
    레벨에 따라 st.session_state.target_speed를 초기화하고 반환
    브라우저 playbackRate 제어 (클라이언트 측)
    - 왕초보: 0.6 (매우 느림)
    - 초급: 0.8 (느림)
    - 중급: 1.0 (보통)
    - 고급: 1.1 (빠름)
    """
    speed_map = {
        "Wangchobo (왕초보)": 0.6,
        "Beginner (초급)": 0.8,
        "Intermediate (중급)": 1.0,
        "Advanced (고급)": 1.1
    }
    target_speed = speed_map.get(level_value, 1.0)
    st.session_state.target_speed = target_speed
    return target_speed

def get_target_speed() -> float:
    """현재 설정된 target_speed 반환 (없으면 1.0)"""
    return st.session_state.get('target_speed', 1.0)

def update_target_speed(level_value: str) -> float:
    """
    레벨 변경 시 target_speed를 즉시 업데이트하고 
    session_update_required 플래그를 설정
    """
    new_speed = initialize_target_speed(level_value)
    st.session_state.session_update_required = True
    st.session_state.speed_changed_at = time.time()
    return new_speed

def get_correction_style_instruction(correction_style: str) -> str:
    """
    교정 강도(Minor/Major) 지시문 생성.
    Minor: 사용자가 한 말의 단어/표현을 최대한 그대로 유지하고
           문법/시제/관사/어순 같은 최소 요소만 고친다.
    Major: 사용자의 의도(의미)는 유지하되, 더 자연스럽고 정확한
           원어민식 표현으로 다시 작성해도 된다.
    """
    style = (correction_style or "Minor").strip().lower()
    if style == "major":
        return (
            "\n### CORRECTION STYLE: MAJOR — REWRITE INTO EVERYDAY NATIVE ENGLISH ###\n"
            "\n"
            "MISSION: Your job is NOT to keep the user's wording. Your job is to REWRITE\n"
            "their sentence the way an ordinary native English speaker would actually say it\n"
            "in casual everyday conversation.\n"
            "\n"
            "MANDATORY REWRITE RULE (READ CAREFULLY):\n"
            "- DO NOT echo the user's sentence back unchanged just because it is grammatical.\n"
            "- Grammatical ≠ natural. Most learner sentences are grammatical but sound textbook,\n"
            "  stiff, or word-for-word translated. You MUST rewrite those into native phrasing.\n"
            "- Keep ONLY the MEANING / INTENT. Everything else — vocabulary, idioms, sentence\n"
            "  structure, length, register — is yours to change.\n"
            "- Do NOT say 'Perfect.' before repeating the user's sentence. In MAJOR mode there\n"
            "  is almost never a 'Perfect' case. Always look for a more natural form first.\n"
            "- The only time you may keep the user's wording as-is is when it already sounds\n"
            "  exactly like everyday native speech you'd actually hear from a friend (very rare).\n"
            "\n"
            "STYLE TARGET — write the way Americans actually talk in daily life:\n"
            "- Use contractions: I'm, you're, don't, gonna, wanna, it's.\n"
            "- Use common idioms and phrasal verbs: grab, hang out, run into, end up, work out.\n"
            "- Drop unnecessary words ('very', 'really', 'I think that').\n"
            "- Short and punchy is better than long and formal.\n"
            "- Avoid translation-flavored English ('I want to go to restaurant for eat dinner').\n"
            "- It is FINE — even expected — that the rewrite looks quite different from the input.\n"
            "\n"
            "CONCRETE TRANSFORMATION EXAMPLES (this is the level of change expected):\n"
            "  User : \"I want to go restaurant for eat dinner with my friend.\"\n"
            "  MAJOR: \"I'm gonna grab dinner with a friend.\"\n"
            "\n"
            "  User : \"Yesterday I was very tired because I worked many hours.\"\n"
            "  MAJOR: \"Yesterday wiped me out — I worked a ton of hours.\"\n"
            "\n"
            "  User : \"I think the weather today is very good.\"\n"
            "  MAJOR: \"It's gorgeous out today.\"\n"
            "\n"
            "  User : \"I went there yesterday.\"   ← grammatical, but flat\n"
            "  MAJOR: \"I dropped by there yesterday.\"   (or \"I swung by yesterday.\")\n"
            "\n"
            "  User : \"I like coffee very much.\"\n"
            "  MAJOR: \"I'm a huge coffee person.\"   (or \"I'm really into coffee.\")\n"
            "\n"
            "  User : \"It is difficult for me to wake up early in the morning.\"\n"
            "  MAJOR: \"I have a hard time waking up early.\"\n"
            "\n"
            "FORBIDDEN IN MAJOR MODE:\n"
            "- Echoing the user's sentence verbatim.\n"
            "- Saying 'Perfect.' before the corrected sentence (this is for MINOR mode only).\n"
            "- Adding new facts, places, names, or details the user did not mention.\n"
            "- Switching to formal/business register unless the user was clearly formal.\n"
        )
    # default: Minor
    return (
        "\n### CORRECTION STYLE: MINOR (PRESERVE USER'S WORDING) ###\n"
        "- The user has selected MINOR correction.\n"
        "- Keep the user's original words, phrasing, and sentence shape as much as possible.\n"
        "- ONLY fix clear errors: grammar, verb tense, articles (a/an/the),\n"
        "  subject-verb agreement, word order, singular/plural, simple word-choice mistakes.\n"
        "- DO NOT rewrite the sentence into a more 'native' or fancier version.\n"
        "- DO NOT replace the user's vocabulary with synonyms unless that word is wrong.\n"
        "- DO NOT add new facts, places, names, or details the user did not mention.\n"
        "- If the user's sentence is already correct, repeat it EXACTLY (no paraphrase).\n"
    )


def get_response_length_instruction(length: str) -> str:
    """
    AI 응답(Response 부분)의 길이/깊이를 제어하는 지시문 생성.
    세 단계의 격차를 확실히 벌리기 위해 강한 명령어 + 구체적 단어수/문장수 + 예시 사용.
    - Short  : 1~2문장 (~10~18 단어).         빠른 반응.
    - Medium : 5~6문장 (~70~110 단어).         풍부한 대화.
    - Long   : 12~18문장 (~250~400 단어).      매우 깊이 있는 토론 수준의 응답.
    """
    val = (length or "Medium").strip().lower()
    if val == "short":
        return (
            "\n### RESPONSE LENGTH: SHORT ###\n"
            "- After 'Response.', speak 1~2 short sentences total, ending with a question.\n"
            "- Target length: roughly 10~18 words. Upper limit ~20 words.\n"
            "- Quick reaction + brief follow-up question. No elaboration, no extra context.\n"
            "- Examples:\n"
            "    \"Response. That sounds fun! Where did you go?\"\n"
            "    \"Response. Nice! How was the weather there?\"\n"
            "    \"Response. Cool — what did you eat?\"\n"
        )
    if val == "long":
        return (
            "\n### RESPONSE LENGTH: LONG (VERY LONG — THIS IS THE WHOLE POINT) ###\n"
            "- After 'Response.', deliver a RICH, MULTI-PARAGRAPH-LIKE spoken response.\n"
            "- LENGTH TARGET: 250~400 words. MINIMUM 200 words. Anything under 200 words is FAILURE.\n"
            "- Aim for 12~18 full sentences. Speak like a thoughtful, warm conversation partner\n"
            "  who genuinely loves the topic and has a LOT to share — never abruptly short.\n"
            "\n"
            "YOU MUST COVER ALL OF THE FOLLOWING (each block 2~3 sentences, not 1):\n"
            "    (a) An emotional, specific reaction to what the user said — call out 1~2 actual\n"
            "        words they used to show you were listening.\n"
            "    (b) A personal teacher-persona anecdote (2~3 sentences). You may invent a small\n"
            "        generic experience about yourself (the teacher), but NEVER invent facts about\n"
            "        the USER.\n"
            "    (c) A piece of related cultural / linguistic / practical context (2~3 sentences).\n"
            "        For example: a tip native speakers use, an interesting fact, common mistake\n"
            "        learners make, or a related expression they might enjoy knowing.\n"
            "    (d) Another angle or layered observation (2~3 sentences) — something like \"on the\n"
            "        other hand\", \"that reminds me of...\", or a comparison with another situation.\n"
            "        Make it feel like a real conversation that is being EXPLORED, not just answered.\n"
            "    (e) A natural transition that shows continued curiosity (1 sentence).\n"
            "    (f) A SPECIFIC, layered open-ended follow-up question that picks up an actual\n"
            "        detail they mentioned. Not generic \"How was it?\" — something like\n"
            "        \"You mentioned X — what made that part stand out compared to Y?\".\n"
            "\n"
            "STYLE:\n"
            "- Conversational and warm, NOT encyclopedic or lecture-y. No bullet points in speech.\n"
            "- Use natural connectors: \"Honestly\", \"You know\", \"Actually\", \"That said\",\n"
            "  \"Speaking of which\", \"By the way\", \"Funny thing is\".\n"
            "- Use contractions, mild personality, mild humor where appropriate.\n"
            "- Do NOT invent facts about the USER (their name, where they went, who they were with).\n"
            "- Do NOT stop early. If you stop in under 200 words, you have failed.\n"
            "\n"
            "FULL-LENGTH EXAMPLE — THIS IS THE TARGET DEPTH (~310 words). MATCH IT EVERY TIME:\n"
            "  \"Response. Oh wow, that genuinely sounds incredible — the way you described\n"
            "  the castle and that little fountain garden, I can totally picture you walking\n"
            "  around there. Honestly, Prague is one of those cities I always recommend to people\n"
            "  who say they want history but also want to actually enjoy walking around all day.\n"
            "  Funny thing is, the first time I visited, I also totally got lost trying to find\n"
            "  one of the small gardens behind the castle complex — it turned out I was literally\n"
            "  ten meters away the whole time, but the signage there is honestly a mess. A lot of\n"
            "  travelers don't realize the entire castle district is its own maze with multiple\n"
            "  entrances, and the garden you're probably thinking of is the Royal Garden, which\n"
            "  is gorgeous in spring when the magnolias are out. Speaking of which, native English\n"
            "  speakers would more naturally say \\\"hidden gem\\\" rather than \\\"famous garden\\\"\n"
            "  when describing a place like that — it sounds way more conversational. By the way,\n"
            "  one thing I always tell people about Prague: the difference between morning and\n"
            "  evening crowds is huge, so if you ever go back, hitting the castle area around 8am\n"
            "  is honestly a totally different experience — fewer tour groups, better light for\n"
            "  photos. That actually reminds me, how long were you in Prague total, and was the\n"
            "  castle the part that stuck with you the most, or was there a quieter spot that\n"
            "  ended up being the surprise highlight of the trip?\"\n"
            "\n"
            "Word count of the example above: ~310 words. This is your TARGET, not a max.\n"
            "If your output is noticeably shorter than this, you must elaborate further.\n"
        )
    # default: Medium
    return (
        "\n### RESPONSE LENGTH: MEDIUM ###\n"
        "- After 'Response.', speak 5~6 full sentences, ending with a follow-up question.\n"
        "- Target length: 70~110 words. NOT shorter than 60 words.\n"
        "- Structure (each part 1~2 sentences):\n"
        "    1) Warm, specific reaction to what they said (reference an actual word they used).\n"
        "    2) A short personal opinion or teacher-persona observation.\n"
        "    3) A small piece of useful context, tip, or related fact.\n"
        "    4) A natural connecting thought (\"That reminds me...\", \"Speaking of which...\").\n"
        "    5) A SPECIFIC follow-up question (not generic).\n"
        "- Stay conversational, not lecture-style. Use contractions and natural connectors.\n"
        "\n"
        "FULL-LENGTH EXAMPLE (~85 words). Match this level of depth every time:\n"
        "    \"Response. Oh nice, a road trip sounds amazing — there's something about\n"
        "    cracking the windows down and just driving with no real plan that's hard to\n"
        "    beat. I'm a huge fan of those spur-of-the-moment trips myself. Quick tip:\n"
        "    if you ever do another one, picking just one anchor stop and leaving the rest\n"
        "    of the day open usually makes the whole thing way more fun than over-planning.\n"
        "    Speaking of which, where did you actually end up, and was there a stop that\n"
        "    surprised you?\"\n"
        "\n"
        "If your output is noticeably under 60 words, you have failed the MEDIUM target.\n"
    )


def get_speed_instruction(target_speed: float) -> str:
    """
    AI에게 전달할 발화 속도 지시문 생성
    """
    if target_speed <= 0.5:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed MUST be {target_speed}x (VERY SLOW).\n"
            f"Speak EXTREMELY SLOWLY and CLEARLY.\n"
            f"Take long pauses between sentences.\n"
            f"Enunciate each word very carefully and distinctly.\n"
            f"Speak as if teaching a child their very first English words.\n"
            f"Use simple, short sentences only.\n"
        )
    elif target_speed <= 0.75:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed MUST be {target_speed}x (SLOW).\n"
            f"Speak VERY SLOWLY and CLEARLY.\n"
            f"Pause between sentences. Enunciate each word carefully.\n"
            f"Imagine speaking to someone learning English for the first time.\n"
        )
    elif target_speed >= 1.2:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed MUST be {target_speed}x (FAST).\n"
            f"Speak at a QUICK, NATURAL pace like a native speaker.\n"
            f"Do not slow down or over-enunciate.\n"
        )
    else:
        return (
            f"\n### SPEECH SPEED CONTROL ###\n"
            f"Your current speech speed is {target_speed}x (NORMAL).\n"
            f"Speak at a natural, moderate pace.\n"
        )

# 앱 시작 시 초기화 (기본값: 중급)
if 'target_speed' not in st.session_state:
    st.session_state.target_speed = 1.0
if 'session_update_required' not in st.session_state:
    st.session_state.session_update_required = False

class UserLevel(Enum):
    WANGCHOBO = "Wangchobo (왕초보)"
    BEGINNER = "Beginner (초급)"
    INTERMEDIATE = "Intermediate (중급)"
    ADVANCED = "Advanced (고급)"
    
    @property
    def default_speed(self) -> float:
        """Returns the audio playback speed for each level (Realtime API voice speed)"""
        speed_map = {
            "Wangchobo (왕초보)": 0.7,       # Slow for absolute beginners
            "Beginner (초급)": 0.8,          # Slightly slow for beginners
            "Intermediate (중급)": 0.9,      # Near normal speed
            "Advanced (고급)": 1.0           # Normal speed
        }
        return speed_map.get(self.value, 1.0)

class ChatMode(Enum):
    NATURAL = "자연스러운 대화만"
    CORRECTION_CHAT = "교정 후 대답해주기"
    SPARTA = "스파르타 교정"

@dataclass
class DojoConfig:
    level: UserLevel
    mode: ChatMode
    is_missile_mode: bool
    topic: str
    missile_timeout: float = 2.0
    model: str = "gpt-4o-mini-realtime-preview"
    correction_style: str = "Minor"  # "Minor" (원문 보존) or "Major" (자연스러운 재작성)
    response_length: str = "Medium"  # "Short" / "Medium" / "Long" — AI 응답(Response 부분) 분량

class PromptManager:
    @staticmethod
    def build(config: DojoConfig, target_speed: float = None) -> str:
        """
        프롬프트 빌드 - target_speed 값을 받아서 AI에게 속도 지시문 주입
        """
        topic_en = {"여행 (Travel)": "Travel", "비즈니스 (Business)": "Business", "일상 수다 (Daily)": "Daily Chat"}.get(config.topic, config.topic)
        
        # target_speed가 None이면 레벨 기본값 사용
        if target_speed is None:
            target_speed = config.level.default_speed
        
        # [MODIFIED] NOISE FILTERING & INSTRUCTIONS
        prompt = "### CRITICAL NOISE FILTERING ###\n"
        prompt += (
            "1. IGNORE NOISE: If the user's audio input is short, unclear, or sounds like background noise (breathing, typing, static), IGNORE IT completely.\n"
            "2. DO NOT HALLUCINATE: Do not invent words if the input is unintelligible.\n"
            "3. CONFIRMATION: If you are unsure what the user said, ask \"Could you say that again?\" instead of trying to correct it.\n"
            "4. STRICT CORRECTION: Only correct the user's English if they spoke a clear, complete sentence or phrase.\n\n"
        )
        
        # [NEW] Audio Cutoff Fix
        prompt += "### AUDIO OUTPUT INSTRUCTION ###\n"
        if config.is_missile_mode:
            prompt += "IMPORTANT: Start your response with 0.2 seconds of silence before speaking (missile mode: reduce latency).\n\n"
        else:
            prompt += "IMPORTANT: Always start your response with 1 second of silence before speaking. This is to prevent audio cutoff.\n\n"

        # 속도 지시문 생성 (모든 모드에 공통 적용)
        speed_instruction = get_speed_instruction(target_speed)

        # 교정 강도 지시문 생성 (CORRECTION_CHAT, SPARTA 모드에서 사용)
        correction_style_instruction = get_correction_style_instruction(
            getattr(config, "correction_style", "Minor")
        )

        # 응답 길이 지시문 (CORRECTION_CHAT, NATURAL 모드에서 사용 - SPARTA는 echo만 하므로 무관)
        response_length_instruction = get_response_length_instruction(
            getattr(config, "response_length", "Medium")
        )

        # ===== SPARTA MODE: COMPLETE OVERRIDE =====
        if config.mode == ChatMode.SPARTA:
            prompt += "### SPARTA MODE: ECHO CORRECTED SENTENCE ONLY ###\n\n"
            # 교정 강도 지시문 주입
            prompt += correction_style_instruction + "\n"
            
            # Language constraint
            if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "LANGUAGE_POLICY: ENGLISH_ONLY\n"
                    "never_use_korean: TRUE\n"
                    "allow_conversation: FALSE\n"
                    "allow_questions: FALSE\n"
                    "allow_explanation: FALSE\n"
                    "wait_for_user: TRUE\n\n"
                )
            
            prompt += (
                "### YOUR ONLY JOB: ECHO THE CORRECTED SENTENCE ###\n\n"
                "FUNCTION: Wait for user to speak → Correct if needed → Echo the corrected version → STOP\n\n"
                "CRITICAL RULES:\n"
                "1. DO NOT speak first. Wait for the user to say something.\n"
                "2. DO NOT ask questions.\n"
                "3. DO NOT add explanations.\n"
                "4. DO NOT say 'Perfect' or 'No changes needed'.\n"
                "5. ALWAYS start with the word 'Correction' before saying the sentence.\n"
                "6. ONLY echo back: 'Correction.' + <corrected sentence>\n\n"
                "OUTPUT FORMAT (AUDIO):\n"
                "Say out loud: \"Correction.\" then pause briefly, then say the corrected sentence.\n"
                "Example audio: \"Correction. I went there yesterday.\"\n\n"
            )
            
            sparta_style = (getattr(config, "correction_style", "Minor") or "Minor").strip().lower()
            if sparta_style == "major":
                if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                    prompt += (
                        "CORRECTION PROTOCOL (Intermediate/Advanced, MAJOR — rewrite into native English):\n\n"
                        "IF USER SAYS: \"I go there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I dropped by there yesterday.\"\n\n"
                        "IF USER SAYS: \"I went there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I swung by yesterday.\"\n"
                        "(Rewrite into native phrasing even when the user's sentence is grammatical.\n"
                        " NEVER just echo it back unchanged.)\n\n"
                        "LANGUAGE: English only.\n"
                        "FORBIDDEN: Explanations, reasons, greetings, questions, echoing input unchanged.\n"
                    )
                else:
                    prompt += (
                        "CORRECTION PROTOCOL (Beginner, MAJOR — rewrite into native English):\n\n"
                        "IF USER SAYS: \"I go there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I dropped by there yesterday.\"\n\n"
                        "IF USER SAYS: \"I went there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I swung by yesterday.\"\n"
                        "(Rewrite into natural phrasing even when grammatical. Never echo unchanged.)\n\n"
                        "FORBIDDEN: Explanations, reasons, greetings, questions, echoing input unchanged.\n"
                    )
            else:
                if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                    prompt += (
                        "CORRECTION PROTOCOL (Intermediate/Advanced, MINOR):\n\n"
                        "IF USER SAYS: \"I go there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n\n"
                        "IF USER SAYS: \"I went there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n"
                        "(Always say 'Correction' first, even if the sentence is already correct)\n\n"
                        "LANGUAGE: English only.\n"
                        "FORBIDDEN: Explanations, reasons, greetings, questions.\n"
                    )
                else:
                    prompt += (
                        "CORRECTION PROTOCOL (Beginner, MINOR):\n\n"
                        "IF USER SAYS: \"I go there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n\n"
                        "IF USER SAYS: \"I went there yesterday.\"\n"
                        "YOU SAY (AUDIO): \"Correction. I went there yesterday.\"\n"
                        "(Always say 'Correction' first, even if the sentence is already correct)\n\n"
                        "FORBIDDEN: Explanations, reasons, greetings, questions.\n"
                    )
            
            prompt += (
                "\n### IMPORTANT: INITIAL GREETING ###\n"
                "When the session starts, DO NOT say anything.\n"
                "DO NOT say 'Hi', 'Hello', 'Let's start', or ask questions.\n"
                "WAIT silently for the user to speak first.\n"
                "After user speaks, then respond with 'Correction.' + sentence + 'Response.' + reply.\n"
            )
            
            # 속도 지시문 추가 (SPARTA 모드)
            prompt += speed_instruction
            
            return prompt
        
        # ===== MISSILE MODE: ACCUMULATION INSTRUCTION =====
        if config.is_missile_mode:
            prompt += "### INSTRUCTION: HANDLE FRAGMENTED SPEECH ###\n"
            prompt += (
                "The user is playing a game where they must defend against missiles by speaking continuously.\n"
                "You will receive user input in multiple fragments (commits) as they pause and resume speaking.\n"
                "1. Accumulate Context: Do not treat a pause as the end of a thought. Wait for the final trigger.\n"
                "2. Response Trigger: You will only be asked to respond when the user is \"Hit\".\n"
                "3. Action: When you finally respond, combine ALL recent speech fragments into one complete sentence.\n"
                "4. Correction: Provide a correction for the FULL combined sentence.\n\n"
            )

        # ===== NORMAL & CORRECTION MODES =====
        # 1. CRITICAL CONSTRAINTS (최상단 - 절대 규칙)
        prompt += "### CRITICAL INSTRUCTIONS ###\n\n"
        
        # ★★★ 최우선 규칙: 대화 주도 + 질문 강제 ★★★
        prompt += (
            "### 🎯 PRIMARY ROLE: LEAD THE CONVERSATION ###\n"
            "You are an ACTIVE English conversation teacher.\n\n"
            "CRITICAL RULE - ALWAYS END WITH A QUESTION:\n"
            "- EVERY response MUST end with a follow-up question\n"
            "- This is MANDATORY - never end without asking something\n"
            "- Questions should encourage the user to speak more\n"
            "- Use open-ended questions (What, How, Why, Tell me about...)\n"
            "- Keep the conversation flowing naturally like a real conversation\n\n"
            "Additional guidelines:\n"
            "- Take the initiative to guide the conversation\n"
            "- Introduce new related topics when appropriate\n"
            "- Be supportive, friendly, and encouraging at all times\n\n"
        )
        
        # 레벨별 언어 정책 (최우선 강제)
        if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
            prompt += (
                "RULE: NEVER USE KOREAN.\n"
                "Constraint: { \"never_use_korean\": true, \"speak_only_english\": true }\n"
                "You must speak 100% in English. Do NOT translate.\n"
                "Even if the user speaks Korean, reply in English only.\n\n"
            )
        else:
             prompt += (
                "RULE: PROVIDE KOREAN SUPPORT.\n"
                "Use simple English suited for beginners.\n"
                "Always provide Korean translations/explanations.\n\n"
             )

        # 2. ROLE & CONTEXT
        prompt += (
            f"Role: English Conversation Teacher (Active Guide).\n"
            f"Topic: {topic_en}.\n"
            f"Target Level: {config.level.name}.\n"
        )
        
        # Add speech rate guidance based on level
        if config.level == UserLevel.WANGCHOBO:
            prompt += "Speech Rate: Speak slowly and clearly for absolute beginners.\n\n"
        elif config.level == UserLevel.BEGINNER:
            prompt += "Speech Rate: Speak at a moderate, comfortable pace.\n\n"
        elif config.level == UserLevel.ADVANCED:
            prompt += "Speech Rate: Speak at a natural, native-like pace.\n\n"
        else:
            prompt += "\n"

        # 3. MODE SPECIFIC RULES (구조 강제)
        if config.mode == ChatMode.CORRECTION_CHAT:
            prompt += (
                "### MODE: CORRECTION FIRST (STRICT FORMAT) ###\n"
                "You MUST strictly follow this response format for EVERY turn. Do not skip any part.\n\n"
            )
            # 교정 강도 지시문 주입 (Minor / Major)
            prompt += correction_style_instruction + "\n"
            # 규칙 #4 는 correction_style에 따라 다르게 적용 (Major 모드를 막지 않기 위함)
            style_lower = (getattr(config, "correction_style", "Minor") or "Minor").strip().lower()
            if style_lower == "major":
                rule4 = (
                    "4. (MAJOR style) Even if the user's sentence is already grammatically correct,\n"
                    "   you MUST still rewrite it into a more natural, everyday native-speaker form.\n"
                    "   Only keep it unchanged if it already sounds exactly like casual native speech.\n"
                    "   Do NOT say 'Perfect.' — that 'Perfect' shortcut is for MINOR mode only.\n"
                )
            else:
                rule4 = (
                    "4. (MINOR style) If the sentence is already correct, repeat it exactly\n"
                    "   (no paraphrase). Say 'Perfect.' then echo the user's original sentence.\n"
                )
            prompt += (
                "### MEANING PRESERVATION (CRITICAL) ###\n"
                "1. Preserve the user's original meaning exactly.\n"
                "2. Follow the CORRECTION STYLE rule above (Minor or Major) when deciding how much to change.\n"
                "3. DO NOT add new facts, locations, or details not said by the user.\n"
                + rule4 + "\n"
            )
            prompt += (
                "### 🤫 SESSION START: WAIT FOR USER ###\n"
                "When the session starts, DO NOT speak first.\n"
                "DO NOT greet the user. DO NOT say 'Hi', 'Hello', 'Let's start', or introduce the topic on your own.\n"
                "DO NOT ask any opening question.\n"
                "WAIT SILENTLY until the user speaks first.\n"
                "Your FIRST audio output of the session MUST already be in the 'Correction. ... Response. ...' format,\n"
                "applied to the user's first sentence. No exceptions.\n\n"
            )

            # AUDIO OUTPUT FORMAT 절차 — style별로 step 2 가 다름
            if style_lower == "major":
                prompt += (
                    "AUDIO OUTPUT FORMAT (MANDATORY, EVERY TURN INCLUDING THE FIRST):\n"
                    "1. Say 'Correction' out loud first.\n"
                    "2. Then say the REWRITTEN sentence (everyday native English version of the user's idea).\n"
                    "   - DO NOT say 'Perfect.' before it — even if the user's sentence was grammatically correct,\n"
                    "     in MAJOR mode you still rewrite it into a more natural native form.\n"
                    "   - The rewrite SHOULD look different from the user's input — that is the point.\n"
                    "3. Then say 'Response' out loud.\n"
                    "4. MANDATORY: Continue with a comment AND end with a follow-up question.\n\n"
                )
            else:
                prompt += (
                    "AUDIO OUTPUT FORMAT (MANDATORY, EVERY TURN INCLUDING THE FIRST):\n"
                    "1. Say 'Correction' out loud first.\n"
                    "2. Then say the corrected sentence.\n"
                    "   - If the sentence has errors: Say the corrected version (minimal change, preserve user's words).\n"
                    "   - If the sentence is perfect: Say 'Perfect' THEN repeat the user's original sentence EXACTLY.\n"
                    "3. Then say 'Response' out loud.\n"
                    "4. MANDATORY: Continue with a comment AND end with a follow-up question.\n\n"
                )

            prompt += (
                "QUESTION REQUIREMENT:\n"
                "- NEVER end your response without asking a question\n"
                "- Questions must encourage the user to share more details\n"
                "- Use: What...? How...? Why...? Tell me more about...? Have you...?\n\n"
            )

            # 응답 길이 지시문 주입 (Response 분량 제어)
            prompt += response_length_instruction + "\n"

            # EXAMPLES — style별로 완전히 다른 예시 사용
            if style_lower == "major":
                prompt += (
                    "EXAMPLES (MAJOR STYLE — REWRITES, NOT ECHOES):\n"
                    "User: \"I go there yesterday.\"\n"
                    "You:  \"Correction. I dropped by there yesterday. Response. Nice! What made you head over?\"\n\n"
                    "User: \"I went there yesterday.\"\n"
                    "You:  \"Correction. I swung by yesterday. Response. Cool — how'd it go?\"\n\n"
                    "User: \"I like coffee very much.\"\n"
                    "You:  \"Correction. I'm a huge coffee person. Response. Same here! What's your go-to order?\"\n\n"
                    "User: \"I want to go restaurant for eat dinner with my friend.\"\n"
                    "You:  \"Correction. I'm gonna grab dinner with a friend. Response. Sounds fun — where are you guys headed?\"\n\n"
                    "User: \"Yesterday I was very tired because I worked many hours.\"\n"
                    "You:  \"Correction. Yesterday wiped me out — I worked a ton of hours. Response. Oof, that's rough. What kept you so busy?\"\n\n"
                    "User: \"It is difficult for me to wake up early in the morning.\"\n"
                    "You:  \"Correction. I have a hard time waking up early. Response. I feel that. What time do you usually crawl out of bed?\"\n\n"
                    "IMPORTANT: Notice that in EVERY example above, the correction is a REWRITE,\n"
                    "not a copy of the user's words. Even when the user's sentence is grammatically fine,\n"
                    "you rewrite it into something a native speaker would actually say.\n\n"
                )
            else:
                prompt += (
                    "EXAMPLES (MINOR STYLE — PRESERVE USER'S WORDING):\n"
                    "User: \"I go there yesterday.\"\n"
                    "You:  \"Correction. I went there yesterday. Response. That sounds interesting! What did you do there?\"\n\n"
                    "User: \"I went there yesterday.\"\n"
                    "You:  \"Correction. Perfect. I went there yesterday. Response. That sounds great! How was the weather?\"\n\n"
                    "User: \"I like coffee.\"\n"
                    "You:  \"Correction. Perfect. I like coffee. Response. Me too! What's your favorite type of coffee?\"\n\n"
                )
            
            # [Correction] 파트 디테일 — style별로 분기
            if style_lower == "major":
                if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                    prompt += (
                        "INSTRUCTIONS (Advanced/Intermediate, MAJOR):\n"
                        "1. [Correction]: ALWAYS speak a REWRITTEN native-English version in English.\n"
                        "   - The rewrite should sound like everyday conversational native speech.\n"
                        "   - NEVER say 'Perfect.' — even grammatical inputs get rewritten in MAJOR mode.\n"
                        "   - It's expected and good that the rewrite differs noticeably from the user's input.\n"
                    )
                else:
                    prompt += (
                        "INSTRUCTIONS (Beginner, MAJOR):\n"
                        "1. [Correction]: ALWAYS speak a REWRITTEN natural-English version.\n"
                        "   - Use simple but native phrasing (contractions, common idioms).\n"
                        "   - You may add a brief Korean hint after the English rewrite.\n"
                        "   - NEVER say 'Perfect.' — always look for a more natural form first.\n"
                    )
            else:
                if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                    prompt += (
                        "INSTRUCTIONS (Advanced/Intermediate, MINOR):\n"
                        "1. [Correction]: ALWAYS echo the corrected (or perfect) sentence in English.\n"
                        "   - If error exists: Just say the corrected sentence (minimal change).\n"
                        "   - If perfect: Say 'Perfect.' then repeat the original sentence exactly.\n"
                    )
                else:
                    prompt += (
                        "INSTRUCTIONS (Beginner, MINOR):\n"
                        "1. [Correction]: ALWAYS echo the corrected (or perfect) sentence.\n"
                        "   - If error exists: Say corrected sentence (you may add brief Korean hint).\n"
                        "   - If perfect: Say 'Perfect. 완벽해요.' then repeat the original sentence.\n"
                    )

            # [Response] 파트 디테일 - 질문 강제
            if config.level in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "2. [Response]: MANDATORY FORMAT\n"
                    "   Structure: [Brief comment] + [Follow-up question]\n"
                    "   - First: React to what they said (That's interesting! / Great! / I see...)\n"
                    "   - Then: ALWAYS end with a question (What...? How...? Why...? Tell me...?)\n"
                    "   - No Korean allowed\n"
                    "   Example: \"That's fascinating! What made you choose that place?\"\n"
                )
            else:
                prompt += (
                    "2. [Response]: MANDATORY FORMAT\n"
                    "   Structure: [Brief comment in English] + [Question in English] + [Korean translation]\n"
                    "   - First: React positively (Great! / Nice! / That's cool!)\n"
                    "   - Then: ALWAYS ask a question to continue (What...? How...?)\n"
                    "   - Finally: Provide Korean translation\n"
                    "   Example: \"That's great! What did you eat there? (거기서 뭐 드셨어요?)\"\n"
                )

        else:  # NATURAL mode
            prompt += (
                "### MODE: NATURAL CONVERSATION ###\n"
                "Lead the conversation actively like a friendly teacher.\n\n"
            )
            # 응답 길이 지시문 주입 (자연 대화 분량 제어)
            prompt += response_length_instruction + "\n"
            prompt += (
                "RESPONSE FORMAT (MANDATORY):\n"
                "- React to what the user said\n"
                "- Share a brief thought or comment\n"
                "- ALWAYS end with a follow-up question (REQUIRED)\n\n"
                "QUESTION TYPES TO USE:\n"
                "- Open-ended: \"What do you think about...?\" \"How do you feel when...?\"\n"
                "- Experience-based: \"Have you ever...?\" \"Tell me about a time when...\"\n"
                "- Opinion: \"Why do you prefer...?\" \"What's your favorite...?\"\n"
                "- Details: \"What happened next?\" \"How did you...?\"\n\n"
                "GUIDELINES:\n"
                "- Start with a warm greeting and introduce the topic\n"
                "- Share interesting facts or personal opinions to make it conversational\n"
                "- Only correct critical errors that interfere with understanding\n"
                "- Keep the energy positive and supportive\n"
                "- NEVER end a response without asking a question\n\n"
            )
            if config.level not in [UserLevel.INTERMEDIATE, UserLevel.ADVANCED]:
                prompt += (
                    "For Beginners:\n"
                    "- Use simple English questions\n"
                    "- Always provide Korean translation after your English response\n"
                    "- Example: \"That's nice! What's your favorite food? (가장 좋아하는 음식이 뭐예요?)\"\n\n"
                )
        
        # 속도 지시문 추가 (NORMAL & CORRECTION 모드)
        prompt += speed_instruction
        
        # 최종 강조
        prompt += (
            "\n### ⚠️ FINAL REMINDER ###\n"
            "EVERY response MUST end with a question to keep the conversation going.\n"
            "This is not optional - it's a core requirement for effective conversation practice.\n"
        )
            
        return prompt

def build_instructions_from_dict(settings: Dict[str, Any], target_speed: float = None) -> str:
    """
    설정 딕셔너리에서 지시문 생성
    target_speed: st.session_state.target_speed에서 전달받은 속도값
    """
    try: level = UserLevel(settings.get("level", UserLevel.INTERMEDIATE.value))
    except: level = UserLevel.INTERMEDIATE
    try: mode = ChatMode(settings.get("mode", ChatMode.NATURAL.value))
    except: mode = ChatMode.NATURAL
    
    # target_speed가 None이면 settings에서 추출하거나 레벨 기본값 사용
    if target_speed is None:
        target_speed = settings.get("target_speed", level.default_speed)
    
    # correction_style 안전 추출 (Minor/Major 외 값이 들어오면 Minor로 폴백)
    raw_style = str(settings.get("correction_style", "Minor")).strip().capitalize()
    if raw_style not in ("Minor", "Major"):
        raw_style = "Minor"

    # response_length 안전 추출 (Short/Medium/Long 외 값이면 Medium으로 폴백)
    raw_len = str(settings.get("response_length", "Medium")).strip().capitalize()
    if raw_len not in ("Short", "Medium", "Long"):
        raw_len = "Medium"

    config = DojoConfig(
        level=level, mode=mode, is_missile_mode=settings.get("is_missile_mode", False),
        topic=settings.get("topic", "Daily"), missile_timeout=settings.get("missile_timeout", 2.0),
        model=settings.get("model", "gpt-4o-mini-realtime-preview"),
        correction_style=raw_style,
        response_length=raw_len,
    )
    return PromptManager.build(config, target_speed)

def get_audio_speed_from_settings(settings: Dict[str, Any]) -> float:
    """Extract voice speed based on user level for Realtime API"""
    try: 
        level = UserLevel(settings.get("level", UserLevel.INTERMEDIATE.value))
        return level.default_speed
    except: 
        return 1.0

def get_voice_speed_from_level(level_value: str) -> float:
    """
    Realtime API voice speed 반환 (0.6 ~ 1.2 범위)
    - 왕초보: 0.7 (느림)
    - 초급: 0.8 (약간 느림)
    - 중급: 0.9 (보통)
    - 고급: 1.0 (정상)
    """
    speed_map = {
        "Wangchobo (왕초보)": 0.7,
        "Beginner (초급)": 0.8,
        "Intermediate (중급)": 0.9,
        "Advanced (고급)": 1.0
    }
    return speed_map.get(level_value, 1.0)


# ===============================================
# HTML / JS (Debug Enhanced)
# ===============================================

REALTIME_CLIENT_HTML_TEMPLATE = r"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI English Dojo</title>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  
  <!-- [DEBUG] Global Log Capture System (Must be first) -->
  <script>
    (function() {
        window.logBuffer = [];
        const originalLog = console.log;
        const originalWarn = console.warn;
        const originalError = console.error;
        
        function formatArgs(args) {
            return args.map(arg => {
                if (typeof arg === 'object') {
                    try { return JSON.stringify(arg); } catch(e) { return String(arg); }
                }
                return String(arg);
            }).join(' ');
        }
        
        function pushLog(level, args) {
            const time = new Date().toISOString().split('T')[1].slice(0, -1);
            window.logBuffer.push(`[${time}] [${level}] ${formatArgs(args)}`);
            // Limit buffer size
            if (window.logBuffer.length > 5000) window.logBuffer.shift();
        }

        console.log = function(...args) { pushLog('INFO', args); originalLog.apply(console, args); };
        console.warn = function(...args) { pushLog('WARN', args); originalWarn.apply(console, args); };
        console.error = function(...args) { pushLog('ERROR', args); originalError.apply(console, args); };
        
        window.downloadLogs = function() {
            const blob = new Blob([window.logBuffer.join('\n')], {type: 'text/plain'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `debug_log_${Date.now()}.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        };
    })();
  </script>

  <style>
    :root { --bg: #f8f9fa; --card-bg: #fff; --primary: #007bff; --text: #212529; --shadow: 0 4px 12px rgba(0,0,0,0.1); --err: #dc3545; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 20px; margin: 0; overflow-y: hidden; }
    
    .status-bar { display: flex; justify-content: space-between; align-items: center; background: var(--card-bg); padding: 15px 25px; border-radius: 12px; box-shadow: var(--shadow); margin-bottom: 20px; font-weight: 600; }
    
    button { padding: 12px 24px; cursor: pointer; border: none; background: var(--primary); color: white; border-radius: 8px; font-weight: bold; margin: 0 5px; }
    button:disabled { opacity: 0.5; cursor: not-allowed; background: #6c757d; }
    
    .log-box { font-family: monospace; font-size: 11px; color: #333; height: 120px; overflow-y: auto; background: #f1f3f5; padding: 10px; border-radius: 8px; margin-top: 10px; border: 1px solid #dee2e6; }
    
    /* === MISSILE MODE UI === */
    .missile-box { height: 180px; background: #e9ecef; border-radius: 12px; position: relative; overflow: hidden; margin-bottom: 20px; display: none; border: 2px solid #dee2e6; }
    .missile-box.active { display: block; }
    
    .ninja-icon { position: absolute; top: 50%; left: 50px; transform: translateY(-50%) scaleX(-1); font-size: 80px; z-index: 5; }
    .robot-icon { position: absolute; top: 50%; right: 50px; transform: translateY(-50%); font-size: 80px; z-index: 5; }
    
    /* 미사일 애니메이션 (좌표 보정됨) */
    @keyframes fly {
        0% { right: 100px; opacity: 1; transform: translateY(-50%) scale(1); }
        90% { right: calc(100% - 120px); opacity: 1; transform: translateY(-50%) scale(1); }
        100% { right: calc(100% - 100px); opacity: 0; transform: translateY(-50%) scale(1.5); }
    }
    
    .missile-obj {
        position: absolute;
        top: 50%;
        right: 100px; /* 로봇 앞 */
        font-size: 40px;
        transform: translateY(-50%);
        display: none;
        z-index: 10;
    }
    
    .missile-obj.firing {
        display: block;
        animation: fly var(--flight-time, 0.6s) ease-in forwards;
    }

    /* === LIVE TRANSCRIPT UI === */
    .transcript-box {
        background: #fff;
        border: 2px solid #007bff;
        border-radius: 12px;
        padding: 15px;
        margin: 15px 0;
        box-shadow: 0 4px 12px rgba(0,123,255,0.1);
        max-height: 320px;
        overflow-y: auto;
        scroll-behavior: smooth;
    }
    .transcript-empty { text-align:center; color:#adb5bd; font-style:italic; padding:20px 0; }
    .t-row {
        margin-bottom: 10px;
        font-size: 15px;
        line-height: 1.45;
        padding: 8px 10px;
        border-radius: 8px;
        background: #f8f9fa;
        display: flex;
        gap: 8px;
        align-items: flex-start;
    }
    .t-row.t-user { background: #fff8ec; border-left: 3px solid #fd7e14; }
    .t-row.t-ai   { background: #eaf4ff; border-left: 3px solid #007bff; }
    .t-label {
        display: inline-block;
        min-width: 56px;
        font-weight: 800;
        text-transform: uppercase;
        font-size: 11px;
        padding-top: 2px;
    }
    .t-user .t-label { color: #fd7e14; }
    .t-ai .t-label { color: #007bff; }
    .t-content { font-weight: 500; color: #343a40; flex: 1; word-break: break-word; }
    .t-ts { font-size: 10px; color: #868e96; margin-left: 6px; white-space: nowrap; padding-top: 4px; }
    .t-clear-btn {
        font-size: 11px; padding: 4px 10px; background:#e9ecef; color:#495057;
        border:1px solid #ced4da; border-radius:6px; cursor:pointer; margin-left:auto;
    }
    .t-clear-btn:hover { background:#dee2e6; }
    
    .mic-meter { width: 200px; height: 10px; background: #ddd; border-radius: 5px; overflow: hidden; margin: 0 auto 10px; }
    .mic-bar { height: 100%; background: #28a745; width: 0%; transition: width 0.05s; }
    .button-container { display: flex; justify-content: center; margin-bottom: 10px; }

    /* === ERROR BANNER === */
    .err-banner {
        background: #fff5f5;
        border: 2px solid #dc3545;
        border-radius: 12px;
        padding: 14px 18px;
        margin: 15px 0;
        color: #842029;
        box-shadow: 0 4px 12px rgba(220,53,69,0.15);
    }
    .err-title { font-size: 16px; font-weight: 800; color: #dc3545; margin-bottom: 6px; }
    .err-meta  { font-size: 12px; color: #6c757d; margin-bottom: 6px; font-family: monospace; }
    .err-detail {
        font-family: monospace; font-size: 12px;
        background: #fff; border: 1px solid #f5c2c7; border-radius: 6px;
        padding: 8px; max-height: 160px; overflow: auto; white-space: pre-wrap; word-break: break-all;
    }
    .err-hint { margin-top: 8px; font-size: 13px; color: #495057; }
  </style>
</head>
<body>
  <div id="root"></div>
  <audio id="remoteAudio" autoplay playsinline></audio>
  <script id="dogo-settings" type="application/json">__SETTINGS_JSON__</script>
  <script id="dogo-session"  type="application/json">__SESSION_JSON__</script>

<script>
  // === GLOBAL VARIABLES for Logic Control ===
  let pc = null, dc = null, micStream = null;
  let missileWaitTimer = null;   // 0.5초 대기 타이머
  let missileFlightTimer = null; // 비행 시간 타이머
  let isMissileActive = false;
  let userTalkActive = false;   // ★ Push-to-Talk: 사용자가 명시적으로 Talk 버튼을 눌렀는지
  let pendingUserItems = [];     // AI 말 중 수집한 사용자 발화 아이템
  let aiSpeaking = false;        // AI 발화 구간 플래그
  let lastAudioDeltaTs = 0;      // 마지막 response.audio.delta 도착 시각 (ms) - watchdog용
  let aiSpeakingWatchdog = null; // aiSpeaking 안전 해제 타이머
  const AI_SPEAKING_SILENCE_MS = 5000; // 5초간 오디오 델타 없으면 응답 끝난 것으로 간주
  
  // [IMPORTANT] VAD State for Manual Detection
  let vadState = "SILENCE"; 
  let lastSpeechTime = 0;
  let speechStartTime = null; // 후보 발화 시작 시각 (잡음 필터용)
  let lastAvg = 0;            // 급격한 소리 변화 감지용
  let speechActive = false;   // 현재 발화 블록 활성 여부
  let speechAccumMs = 0;      // 누적 발화 시간(ms)
  let lastVoiceMs = 0;        // 마지막 음성 감지 시각
  
  window.debug_vad_count = 0;    // VAD 디버그 카운터

  window.log = function(msg, type="norm") {
      const el = document.querySelector(".log-box");
      if(el) { 
          const color = type === 'err' ? 'red' : (type === 'sys' ? 'blue' : 'black');
          el.innerHTML += `<div style="color:${color}">[${new Date().toLocaleTimeString()}] ${msg}</div>`; 
          el.scrollTop = el.scrollHeight; 
      }
      console.log(msg);
  }

  // ★ CENTRALIZED EVENT PROCESSOR (Manual VAD -> Here -> Logic) ★
  // Returns false if echo-blocked (caller must revert vadState)
  function processVadEvent(type) {
      // 1. SPEECH STARTED (Cancel Attack)
      if (type === 'input_audio_buffer.speech_started') {
          if (aiSpeaking) {
              console.warn("%c[ECHO BLOCKED] 🛡️ AI is speaking! Ignoring VAD.", "background: red; color: white; font-weight: bold");
              return false;
          }

          window.debug_vad_count++;
          console.log(`%c[VAD #${window.debug_vad_count}] 🎤 START`, 'color: yellow; background: #333; font-weight: bold');
          
          if(window.updateStatus) window.updateStatus({userSpk: true});

          if (missileWaitTimer || missileFlightTimer) {
              console.log("%c[DEFENSE] 🛡️ Attack Canceled! (User kept speaking)", "color: orange; font-weight: bold");
              clearTimeout(missileWaitTimer);
              clearTimeout(missileFlightTimer);
              missileWaitTimer = null;
              missileFlightTimer = null;
              isMissileActive = false;
              if(window.updateStatus) window.updateStatus({ firing: false, hit: false });
          }
          return true;
      }
      
      // 2. SPEECH STOPPED (Commit & Launch)
      if (type === 'input_audio_buffer.speech_stopped') {
          if (aiSpeaking) {
              console.warn("%c[ECHO BLOCKED] 🛡️ AI speaking! Ignoring speech_stopped.", "background: red; color: white; font-weight: bold");
              return false;
          }

          console.log(`%c[VAD #${window.debug_vad_count}] 🔇 STOP`, 'color: #ccc; background: #333');
          // PTT: 한 차례 말이 끝났으니 다음 발화는 다시 Talk 버튼이 필요
          userTalkActive = false;
          if(window.updateStatus) window.updateStatus({userSpk: false});

          if (SETTINGS.is_missile_mode) {
              // Missile Mode: commit하지 않음 → 버퍼에 축적, HIT 시 한 번에 commit
              console.log("%c[MISSILE] 📦 Audio buffered (not committed)", "color: #ffc107");

              console.log("%c[TIMER] ⏳ Wait Timer STARTED (0.5s)", "color: orange");
              missileWaitTimer = setTimeout(() => {
                  missileWaitTimer = null;
                  console.log("%c[MISSILE] 🚀 LAUNCHED!", "color: white; background: #dc3545; font-weight: bold");
                  
                  if(window.updateStatus) window.updateStatus({ firing: true });
                  isMissileActive = true;

                  const audioEl = document.getElementById("remoteAudio");
                  console.log("[DEBUG] 🚀 Launching! Muting Audio (stream alive). CurrentTime:", audioEl ? audioEl.currentTime : "null");
                  // [핵심 변경] pause() 대신 muted=true 사용 - 스트림은 유지하고 음소거만
                  if(audioEl) {
                      audioEl.muted = true;
                      // 혹시 멈춰있다면 다시 재생 (라이브 스트림 유지)
                      if (audioEl.paused) {
                          audioEl.play().catch(e => console.warn("[AUDIO] Resume during launch failed", e));
                      }
                  }

                  const flightTimeMs = (SETTINGS.missile_duration || 0.6) * 1000;
                  missileFlightTimer = setTimeout(() => {
                      missileFlightTimer = null;
                      if (isMissileActive) {
                          console.log("%c[HIT] 💥 Impact! Committing ALL audio & Requesting Response...", "color: #00ff00; background: black; font-weight: bold");
                          if(window.updateStatus) window.updateStatus({ firing: false, hit: true });
                          
                          if(dc && dc.readyState === 'open') {
                              dc.send(JSON.stringify({type: "input_audio_buffer.commit"}));
                              dc.send(JSON.stringify({type: "response.create"}));
                          }
                          
                          const audioEl = document.getElementById("remoteAudio");
                          if(audioEl) {
                              console.log("[DEBUG] 💥 Impact! Unmuting Audio. CurrentTime:", audioEl.currentTime, "Paused:", audioEl.paused);
                              // [핵심 변경] play() 대신 muted=false 로 해제
                              // WebRTC 스트림은 계속 살아있었으므로 즉시 들림
                              audioEl.muted = false;
                              // 만약 어떤 이유로 paused 상태라면 재생
                              if (audioEl.paused) {
                                  audioEl.play().then(() => {
                                      console.log("[AUDIO] 🔊 Resumed after HIT. CurrentTime:", audioEl.currentTime);
                                  }).catch(e => console.error("[AUDIO] Play on HIT failed", e));
                              } else {
                                  console.log("[AUDIO] 🔊 Unmuted on HIT (already playing). CurrentTime:", audioEl.currentTime);
                              }
                          }

                          setTimeout(() => {
                              if(window.updateStatus) window.updateStatus({ hit: false });
                              isMissileActive = false;
                          }, 1500);
                      }
                  }, flightTimeMs);
              }, 500);
          } else {
              console.log("%c[SEND] 📤 Audio Commit + Response", "color: green");
              if(dc && dc.readyState === 'open') {
                  dc.send(JSON.stringify({type: "input_audio_buffer.commit"}));
                  dc.send(JSON.stringify({type: "response.create"}));
              }
          }
          return true;
      }
      return true;
  }

  // Audio Visualizer
  function startVisualizer(stream) {
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);
      const buffer = new Uint8Array(analyser.frequencyBinCount);
      
      function draw() {
          analyser.getByteFrequencyData(buffer);
          let sum = 0;
          let max = 0;
          for(let i=0; i<buffer.length; i++) {
              const v = buffer[i];
              sum += v;
              if (v > max) max = v;
          }
          const avg = sum / buffer.length;
          if(window.updateStatus) window.updateStatus({micLevel: avg});
          
          // ★★★ MANUAL VAD LOGIC (Noise Robust) ★★★
          if (SETTINGS.is_missile_mode) {
              const VAD_THRESHOLD = isMissileActive ? 50 : 35;
              const CANCEL_BOOST = 80;
              const WORD_GAP_MS = 600;
              const TOTAL_MIN_MS = isMissileActive ? 200 : 400;
              
              const now = Date.now();
              const delta = avg - lastAvg;

              // [핵심] 침묵 중 주기적 버퍼 클리어 (AI 응답 후 쌓이는 침묵/잡음 제거)
              if (vadState === "SILENCE" && !speechActive && window.needsBufferClear) {
                  if (!window.lastBufferClearTime || now - window.lastBufferClearTime > 1000) {
                      if (dc && dc.readyState === 'open') {
                          dc.send(JSON.stringify({type: "input_audio_buffer.clear"}));
                      }
                      window.lastBufferClearTime = now;
                  }
              }

              if (avg > VAD_THRESHOLD || (isMissileActive && avg > CANCEL_BOOST)) {
                  if (!speechStartTime) speechStartTime = now;
                  const duration = now - speechStartTime;

                  const isThumpSpike = (delta > 40 && max > 180 && duration < 300);
                  if (isThumpSpike) {
                      speechStartTime = null;
                  } else {
                      if (!speechActive) speechActive = true;
                      lastVoiceMs = now;
                      speechAccumMs += duration;
                      speechStartTime = now;

                      if (speechAccumMs >= TOTAL_MIN_MS || (isMissileActive && avg > CANCEL_BOOST)) {
                          lastSpeechTime = now;
                          if (vadState === "SILENCE") {
                              // ★ PTT: 미사일 모드에서 주변 소음 차단.
                              //   사용자가 'Talk' 버튼을 눌러 userTalkActive 가 true 가 되기 전에는
                              //   주변 소음/잡담이 mic 임계치를 넘겨도 speech_started 를 발사하지 않는다.
                              if (!userTalkActive) {
                                  // 무시 — 버튼 대기
                                  speechActive = false;
                                  speechAccumMs = 0;
                              } else {
                                  const accepted = processVadEvent('input_audio_buffer.speech_started');
                                  if (accepted) {
                                      vadState = "SPEAKING";
                                      window.needsBufferClear = false;
                                  } else {
                                      speechActive = false;
                                      speechAccumMs = 0;
                                  }
                              }
                          }
                      }
                  }
              } else {
                  speechStartTime = null;
                  if (speechActive && (now - lastVoiceMs > WORD_GAP_MS)) {
                      speechActive = false;
                      speechAccumMs = 0;
                      if (vadState === "SPEAKING") {
                          const accepted = processVadEvent('input_audio_buffer.speech_stopped');
                          if (accepted) {
                              vadState = "SILENCE";
                          }
                      }
                  } else if (vadState === "SPEAKING") {
                      if (now - lastSpeechTime > 500) {
                          const accepted = processVadEvent('input_audio_buffer.speech_stopped');
                          if (accepted) {
                              vadState = "SILENCE";
                          }
                      }
                  }
              }

              lastAvg = avg;
          }
          
          requestAnimationFrame(draw);
      }
      draw();
  }

  window.connectSystem = async function() {
      // === Paused 상태이면 그냥 재개 (새 토큰 없이) ===
      // soft disconnect 로 pc + micStream이 살아있는 경우, 마이크/오디오만 다시 켠다.
      try {
          if (pc && pc.connectionState !== 'closed' && pc.connectionState !== 'failed'
              && micStream && micStream.getAudioTracks().some(t => !t.readyState || t.readyState === 'live')) {
              micStream.getAudioTracks().forEach(t => t.enabled = true);
              const remoteAudio = document.getElementById('remoteAudio');
              if (remoteAudio) remoteAudio.muted = false;
              if (window.updateStatus) window.updateStatus({ conn: "CONNECTED" });
              console.log("%c[CONNECT] Resumed from pause (no new token needed)",
                  "color:#28a745;font-weight:bold");
              return;
          }
      } catch (e) {
          console.warn("[CONNECT] resume check failed, doing full connect:", e);
      }

      try {
          // [NEW] Audio Debug Timer
          if (window.audioDebugTimer) clearInterval(window.audioDebugTimer);
          window.audioDebugTimer = setInterval(() => {
              const el = document.getElementById("remoteAudio");
              if (el && !el.paused) {
                  console.log(`%c[AUDIO TRACK] Time: ${el.currentTime.toFixed(2)}s | Ended: ${el.ended} | Muted: ${el.muted}`, "color: #aaa; font-size: 10px");
              }
          }, 100);

          window.log("Connecting...", "sys");
          micStream = await navigator.mediaDevices.getUserMedia({ 
              audio: {
                  echoCancellation: true,
                  noiseSuppression: true,
                  autoGainControl: true
              } 
          });
          startVisualizer(micStream);
          
          pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
          
          pc.ontrack = (event) => {
              const el = document.getElementById("remoteAudio");
              el.srcObject = event.streams[0];

              // [NEW] Audio Debug Listeners
              el.onplay = () => console.log("%c[EVENT] 🔊 Audio 'play' event fired", "color: lime");
              el.onpause = () => console.log("%c[EVENT] ⏸️ Audio 'pause' event fired", "color: orange");
              el.onseeking = () => console.log("%c[EVENT] ⏩ Audio 'seeking' (Reset?)", "color: magenta");
              el.onvolumechange = () => console.log(`%c[EVENT] 🔈 Audio volumeChange. Muted=${el.muted}`, "color: cyan");

              // [핵심 변경] Mute/Unmute 방식으로 변경
              // WebRTC 스트림은 항상 재생 상태로 유지하고,
              // 미사일 비행 중에는 muted=true 로 침묵, HIT 시 muted=false 로 해제
              if (SETTINGS.is_missile_mode) {
                  el.muted = true;  // 비행 중에는 음소거 (스트림은 살아있음)
                  console.log("[AUDIO] Muted initially for Missile Mode (stream still alive)");
              } else {
                  el.muted = false;
              }
              // 항상 play() 호출 - WebRTC 라이브 스트림은 끊김 없이 유지되어야 함
              el.play().then(() => {
                  console.log("[AUDIO] Stream playing (muted=" + el.muted + ")");
              }).catch(e => console.error("[AUDIO] Initial play failed", e));
          };

          micStream.getTracks().forEach(track => pc.addTrack(track, micStream));
          dc = pc.createDataChannel("oai-events");
          
          dc.onopen = () => {
             window.log("Connected! Session Ready.", "sys");
             console.log("%c[CONNECTION] DataChannel OPEN! Setting status to CONNECTED", "color: green; font-weight: bold");
             if(window.updateStatus) window.updateStatus({conn: "CONNECTED"}); // 강제 업데이트
          };
          
          // === AI Speaking 상태 해제 헬퍼 ===
          // GA API는 Beta 와 다른 완료 이벤트명을 씁니다.
          // 어떤 이벤트로 들어오든 한 번에 깨끗하게 정리할 수 있도록 헬퍼로 분리.
          function endAiSpeaking(reason) {
              if (!aiSpeaking && !aiSpeakingWatchdog) return; // 이미 정리됨
              aiSpeaking = false;
              if (aiSpeakingWatchdog) { clearTimeout(aiSpeakingWatchdog); aiSpeakingWatchdog = null; }
              if (window.updateStatus) window.updateStatus({ aiSpeak: false });
              console.log(`%c[AI DONE] aiSpeaking=false (reason: ${reason})`, "color: #20c997; font-weight: bold");

              if (dc && dc.readyState === 'open') {
                  if (pendingUserItems.length > 0) {
                      pendingUserItems.forEach(({ id }) => {
                          console.log(`%c[AI DONE] 🗑️ Deleting pre-AI-end item: ${id}`, "color: #ff6b6b; font-weight: bold");
                          // [FIX] 잘못된 'id' 필드 제거 — Realtime API 스펙은 item_id만 받음.
                          //   기존: { type, item_id: id, id }  ← 마지막 `id`(shorthand)가 invalid_request_error 유발
                          //   수정: { type, item_id: id }
                          try { dc.send(JSON.stringify({ type: "conversation.item.delete", item_id: id })); } catch(_) {}
                      });
                  }
                  if (SETTINGS.is_missile_mode) {
                      window.needsBufferClear = true;
                      window.lastBufferClearTime = null;
                      console.log("%c[BUFFER] 🔖 Buffer clear scheduled (will clear during silence)", "color: #17a2b8; font-weight: bold");
                  }
              }
              pendingUserItems = [];
          }

          function armAiWatchdog() {
              // 마지막 audio.delta 로부터 AI_SPEAKING_SILENCE_MS 동안 새 델타가 없으면 강제 해제
              if (aiSpeakingWatchdog) clearTimeout(aiSpeakingWatchdog);
              aiSpeakingWatchdog = setTimeout(() => {
                  const idle = Date.now() - lastAudioDeltaTs;
                  if (aiSpeaking && idle >= AI_SPEAKING_SILENCE_MS) {
                      console.warn(`%c[WATCHDOG] AI speaking deadlock detected (idle=${idle}ms). Forcing release.`,
                          "background:#ffc107;color:black;font-weight:bold");
                      endAiSpeaking("watchdog_timeout");
                  } else if (aiSpeaking) {
                      // 아직 델타가 오고 있으면 다시 무장
                      armAiWatchdog();
                  }
              }, AI_SPEAKING_SILENCE_MS + 200);
          }

          dc.onmessage = (e) => {
              let ev;
              try { ev = JSON.parse(e.data); } catch(parseErr) {
                  console.error("[DC] Failed to parse event", parseErr, e.data);
                  return;
              }

              // === 0. CATCHALL: 모든 이벤트 타입을 1줄 로그 (response.*, error 위주) ===
              if (ev.type && (ev.type.startsWith('response.') || ev.type === 'error' || ev.type.includes('failed') || ev.type.includes('cancel'))) {
                  // 너무 시끄러운 audio.delta 는 제외 (이미 별도 카운터로 추적함)
                  if (ev.type !== 'response.audio.delta' && ev.type !== 'response.output_audio.delta'
                      && ev.type !== 'response.audio_transcript.delta' && ev.type !== 'response.output_audio_transcript.delta'
                      && ev.type !== 'response.text.delta') {
                      console.log(`%c[EVT] ${ev.type}`, "color: #6c757d; font-size: 11px");
                  }
              }

              // === 1. VAD (서버 측, 비-미사일 모드에서만 사용) ===
              if (!SETTINGS.is_missile_mode) {
                  if(ev.type === 'input_audio_buffer.speech_started') processVadEvent(ev.type);
                  if(ev.type === 'input_audio_buffer.speech_stopped') processVadEvent(ev.type);
              }

              // === 2. STT (사용자 음성 → 텍스트) ===
              if (ev.type === 'conversation.item.input_audio_transcription.completed') {
                  const text = (ev.transcript || "").trim();
                  const itemId = ev.item_id || ev.conversation_item_id || (ev.item && ev.item.id);
                  console.log(`%c[STT] User said: "${text}"`, 'color: #fd7e14; font-weight: bold; background: #fff3cd; padding: 2px');
                  if (window.updateTranscript) window.updateTranscript({ user: text });
                  if (itemId && aiSpeaking) {
                      pendingUserItems.push({ id: itemId, ts: Date.now() });
                      console.log(`%c[STT] (during AI) item_id stored: ${itemId}`, 'color: #ffb347; font-weight: bold');
                  }
              }

              // === 3. TTS transcript (AI 음성 → 텍스트) - Beta/GA 양쪽 이름 대응 ===
              if (ev.type === 'response.audio_transcript.done' || ev.type === 'response.output_audio_transcript.done') {
                  const text = (ev.transcript || "").trim();
                  console.log(`%c[TTS] AI said: "${text}"`, 'color: #007bff; font-weight: bold; background: #e7f5ff; padding: 2px');
                  if (window.updateTranscript) window.updateTranscript({ ai: text });
              }

              // === 4. AI 응답 시작 ===
              if (ev.type === 'response.created') {
                  aiSpeaking = true;
                  lastAudioDeltaTs = Date.now();
                  armAiWatchdog();
                  if (window.updateStatus) window.updateStatus({ aiSpeak: true });

                  // ★ 직전 인터럽트로 audio element / 트랙이 끊어졌다면 여기서 복구.
                  //   복구하지 않으면 새 AI 응답이 들리지 않음.
                  const remoteAudio = document.getElementById('remoteAudio');
                  if (remoteAudio) {
                      try {
                          // srcObject 복구 (인터럽트 때 null 로 만들어 두었음)
                          if (!remoteAudio.srcObject && window.__savedAudioStream) {
                              remoteAudio.srcObject = window.__savedAudioStream;
                          }
                          remoteAudio.muted = false;
                          remoteAudio.play().catch(() => {});
                      } catch(_) {}
                  }
                  // 수신 audio 트랙 enable (인터럽트 때 disable 했었음)
                  try {
                      if (pc && pc.getReceivers) {
                          pc.getReceivers().forEach(r => {
                              if (r && r.track && r.track.kind === 'audio') {
                                  r.track.enabled = true;
                              }
                          });
                      }
                  } catch(_) {}

                  console.log("%c[AI] Speaking started (response.created)", "color: #20c997; font-weight: bold");
              }

              // === 5. AI 활동 신호 도착 시 watchdog 갱신 ===
              //   GA WebRTC 모드에선 audio.delta 가 안 오고 오디오는 RTP 로 흐른다.
              //   transcript delta 도 함께 heartbeat 로 인정해야 watchdog 가 오발사하지 않음.
              //   (이전 버그: WebRTC 모드인데 audio.delta 만 보고 5초 후 'AI 끝났다'로 잘못 판단 →
              //    이후 인터럽트가 동작 안 함)
              if (ev.type === 'response.audio.delta'
                  || ev.type === 'response.output_audio.delta'
                  || ev.type === 'response.audio_transcript.delta'
                  || ev.type === 'response.output_audio_transcript.delta'
                  || ev.type === 'response.text.delta'
                  || ev.type === 'response.output_text.delta') {
                  lastAudioDeltaTs = Date.now();
              }

              // === 6. AI 응답 종료 (Beta + GA 양쪽의 모든 종료 신호) ===
              //   Beta: response.completed, response.audio.done, response.audio_transcript.done
              //   GA  : response.done, response.output_audio.done, response.output_audio_transcript.done
              //   실패/취소: response.cancelled, response.failed
              const END_TYPES = new Set([
                  'response.completed',
                  'response.done',
                  'response.audio.done',
                  'response.output_audio.done',
                  'response.audio_transcript.done',
                  'response.output_audio_transcript.done',
                  'response.cancelled',
                  'response.failed',
              ]);
              if (END_TYPES.has(ev.type)) {
                  endAiSpeaking(ev.type);
              }

              // === 7. 에러 이벤트 ===
              // 세션 도중에 들어오는 error / response.error 는 대부분 대화 흐름에 치명적이지 않음
              // (예: 잘못된 파라미터 한 개 등). 사용자에게 빨간 배너를 띄우지 않고 콘솔에만 기록한다.
              // 연결 단계 자체가 실패한 경우는 connectSystem() 안에서 별도로 updateError를 호출한다.
              if (ev.type === 'error' || ev.type === 'response.error') {
                  console.error("%c[OPENAI ERROR EVENT - banner suppressed]",
                      "background:#dc3545;color:white;font-weight:bold", ev);
                  // 에러가 났으면 응답이 끝난 것으로 처리해서 잠금 풀어주기
                  endAiSpeaking("error_event");
                  // ⚠️ updateError 호출 의도적 생략: 화면 배너는 띄우지 않음.
              }
          };

          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);

          // ★ 클라우드 구조: SDP 교환을 OpenAI에 직접 보낸다.
          //   임시 토큰(client_secret)은 Streamlit Python이 미리 발급해서
          //   window.__SESSION_DATA__ 로 주입해 두었다.
          const SD = window.__SESSION_DATA__ || {};
          const TOKEN = SD.token;
          const MODEL = SD.model;
          if (!TOKEN || !MODEL) {
              const msg = "세션 토큰이 없습니다. Streamlit 사이드의 'Start / Restart Session' 버튼을 먼저 누르세요.";
              window.log(msg, "err");
              if (window.updateError) {
                  window.updateError({
                      visible: true, stage: "no_token", model: "(none)",
                      message: msg, detail: ""
                  });
              }
              if (window.updateStatus) window.updateStatus({ conn: "ERROR" });
              try { if (pc) pc.close(); } catch(_) {}
              try { if (micStream) micStream.getTracks().forEach(t => t.stop()); } catch(_) {}
              return;
          }

          // OpenAI Realtime GA /v1/realtime/calls 에 SDP offer 직접 POST.
          // Authorization 헤더에는 위에서 받은 ephemeral token 만 들어간다 (real API key 아님).
          const sdpUrl = `https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(MODEL)}`;
          const resp = await fetch(sdpUrl, {
              method: "POST",
              headers: {
                  "Authorization": `Bearer ${TOKEN}`,
                  "Content-Type": "application/sdp",
              },
              body: offer.sdp,
          });

          const rawText = await resp.text();
          // OpenAI는 성공 시 SDP 본문("v=" 시작)을 반환, 실패 시 JSON 에러를 반환한다.
          const sdpLike = /^\s*v=/.test(rawText || "");
          if (!resp.ok || !sdpLike) {
              let errMsg = `HTTP ${resp.status}`;
              let detail = (rawText || "").slice(0, 800);
              try {
                  const j = JSON.parse(rawText);
                  if (j && j.error) {
                      errMsg = (j.error.message || j.error) + "";
                      detail = JSON.stringify(j.error, null, 2);
                  }
              } catch(_) {}
              const isExpired = /expire|invalid.*(token|key|secret)/i.test(errMsg + " " + detail);
              if (isExpired) {
                  errMsg = "세션 토큰이 만료되었습니다 (60초 한도). 사이드의 'Reconnect / Apply Settings'를 다시 누르세요.";
              }
              console.error("%c[CONNECT ERROR]", "background:#dc3545;color:white;font-weight:bold",
                  { status: resp.status, model: MODEL, errMsg, detail });
              window.log(`Connection Failed (${MODEL}): ${errMsg}`, "err");
              if (window.updateError) {
                  window.updateError({
                      visible: true, stage: "sdp_exchange",
                      model: MODEL, message: errMsg, detail
                  });
              }
              if (window.updateStatus) window.updateStatus({ conn: "ERROR" });
              try { if (pc) pc.close(); } catch(_) {}
              try { if (micStream) micStream.getTracks().forEach(t => t.stop()); } catch(_) {}
              return;
          }

          await pc.setRemoteDescription({ type: "answer", sdp: rawText });
          console.log(`%c[CONNECT] ✅ Using model: ${MODEL}`, "color: lime; font-weight: bold");
          window.log(`Connected with model: ${MODEL}`, "sys");

      } catch (e) {
          window.log("Connection Failed: " + e.message, "err");
          console.error("[CONNECT EXCEPTION]", e);
          if (window.updateError) {
              window.updateError({
                  visible: true,
                  stage: "client_exception",
                  model: "(client)",
                  message: e.message || String(e),
                  detail: (e.stack || "").slice(0, 1000),
              });
          }
          if (window.updateStatus) window.updateStatus({ conn: "ERROR" });
      }
  };

  // ★ Push-to-Talk: 사용자가 명시적으로 발화를 시작.
  //   - 미사일 모드: 주변 소음이 mic 임계치를 넘어도 무시되다가, 이 버튼이 눌리면 speech_started 가 발사됨.
  //   - AI 발화 중 호출되면 response.cancel 로 AI 응답을 즉시 끊고, 사용자 발화 모드로 전환.
  //   - 발화 끝은 기존 미사일 모드의 자동 침묵 감지(또는 일반 모드의 server VAD)가 담당.
  window.startTalk = () => {
      try {
          // ★★★ 핵심: aiSpeaking 플래그를 신뢰하지 않는다.
          //   - watchdog 오발사로 aiSpeaking=false 가 되었지만 실제로는 OpenAI 서버가 응답을 계속 생성/전송하는 경우가 있음.
          //   - 이 경우 사용자가 Talk 를 눌렀을 때 인터럽트 분기로 안 가서 response.cancel 이 전송 안 됨 → AI가 끝까지 말함.
          //   해결: 매번 무조건 인터럽트 신호를 송신한다. 응답이 없으면 서버가 알아서 무시한다.

          console.log("%c[INTERRUPT] Talk pressed — unconditionally sending cancel/clear and disabling AI audio track",
              "background:#ff6b00;color:white;font-weight:bold");

          // (a) 서버에 응답 취소 + 입력 버퍼 비우기 (항상 전송)
          if (dc && dc.readyState === 'open') {
              try { dc.send(JSON.stringify({ type: "response.cancel" })); } catch(_) {}
              try { dc.send(JSON.stringify({ type: "input_audio_buffer.clear" })); } catch(_) {}
          }

          // (b) ★ 브라우저 측 디코더 큐의 잔여 오디오까지 즉시 무음으로.
          //   WebRTC 수신 트랙 자체를 enabled=false 로 만들면 트랙이 즉시 silence 를 출력.
          //   잠깐 끄고(다음 frame), 다시 켠다 — 디코더 버퍼를 끊는 효과.
          //   다음 response.created 이벤트에서도 다시 enabled=true 복구함.
          try {
              if (pc && pc.getReceivers) {
                  pc.getReceivers().forEach(r => {
                      if (r && r.track && r.track.kind === 'audio') {
                          r.track.enabled = false;
                      }
                  });
              }
          } catch (rcvErr) {
              console.warn("[INTERRUPT] receiver disable failed:", rcvErr);
          }

          // (c) 오디오 요소 mute + srcObject 끊기 → 디코더 큐에 쌓인 잔여 오디오가 새어나오지 못하게.
          //   ★ 다음 response.created 에서 복구한다 — 여기서 절대 setTimeout 으로 다시 붙이지 말 것.
          //     이전 버그: 50ms 후 srcObject 다시 붙이고 트랙도 enable → 그 사이 OpenAI 서버가 cancel
          //     처리 전에 보낸 인플라이트 오디오 + 디코더 큐의 잔여 오디오가 그대로 흘러나옴.
          //   사용자가 말하는 동안 audio element 는 srcObject=null + tracks disabled 상태로 침묵 유지.
          //   사용자 발화 종료 → 미사일 → 새 response.create → response.created 이벤트 → 거기서 복구.
          const remoteAudio = document.getElementById('remoteAudio');
          if (remoteAudio) {
              try {
                  // 다음에 복구할 수 있도록 stream 참조 저장
                  if (remoteAudio.srcObject) {
                      window.__savedAudioStream = remoteAudio.srcObject;
                  }
                  remoteAudio.muted = true;
                  try { remoteAudio.pause(); } catch(_) {}
                  remoteAudio.srcObject = null;
              } catch(_) {}
          }

          // (d) 미사일 타이머/플래그 청소
          if (missileWaitTimer)   { clearTimeout(missileWaitTimer);   missileWaitTimer = null; }
          if (missileFlightTimer) { clearTimeout(missileFlightTimer); missileFlightTimer = null; }
          isMissileActive = false;
          if (window.updateStatus) window.updateStatus({ firing: false, hit: false });

          // (e) aiSpeaking 강제 해제 (서버에서 response.cancelled 이벤트도 곧 도착함)
          aiSpeaking = false;
          if (aiSpeakingWatchdog) { clearTimeout(aiSpeakingWatchdog); aiSpeakingWatchdog = null; }
          if (window.updateStatus) window.updateStatus({ aiSpeak: false });

          // 2) 마이크 보장: enabled = true
          if (micStream) {
              try { micStream.getAudioTracks().forEach(t => t.enabled = true); } catch(_) {}
          }

          // 3) PTT 게이트 열기
          userTalkActive = true;

          // 4) 미사일 모드면 speech_started 를 직접 발사 (게이트가 닫혀있던 시각화 루프 대신)
          //    일반 모드는 OpenAI 서버 VAD 가 알아서 감지하므로 별도 발사 불필요.
          if (SETTINGS.is_missile_mode) {
              processVadEvent('input_audio_buffer.speech_started');
          }

          if (window.updateStatus) window.updateStatus({ userSpk: true });
          console.log("%c[TALK] 🎤 User talk started", "color:#28a745;font-weight:bold");
      } catch (e) {
          console.error("[TALK] error:", e);
      }
  };

  // Soft pause: PeerConnection을 끊지 않고 마이크/오디오만 끈다.
  // → Connect 를 다시 누르면 새 토큰 없이 즉시 재개됨 (사이드바 버튼 안 눌러도 됨).
  window.disconnectSystem = () => {
      try {
          if (micStream) {
              micStream.getAudioTracks().forEach(t => t.enabled = false);
          }
          const remoteAudio = document.getElementById('remoteAudio');
          if (remoteAudio) remoteAudio.muted = true;
          if (window.updateStatus) window.updateStatus({ conn: "PAUSED", micLevel: 0 });
          console.log("%c[PAUSE] Mic muted. PC kept alive. Press Connect to resume.",
              "color:#fd7e14;font-weight:bold");
      } catch (e) {
          console.error("[PAUSE] error:", e);
      }
  };

  // 강제 종료: 완전히 끊고 정리. 사이드바의 새 세션 시작 직전에만 의미가 있음.
  window.hardDisconnectSystem = () => {
      try { if (pc) pc.close(); } catch(_) {}
      pc = null;
      try { if (micStream) micStream.getTracks().forEach(t => t.stop()); } catch(_) {}
      micStream = null;
      if (window.updateStatus) window.updateStatus({ conn: "IDLE", micLevel: 0 });
  };
</script>

<script type="text/babel">
  let SETTINGS = {};
  try { SETTINGS = JSON.parse(document.getElementById("dogo-settings").textContent); } catch(e) {}
  // Streamlit Python 이 발급한 ephemeral session 데이터 {token, model, instructions, expires_at}
  try {
      const sd = JSON.parse(document.getElementById("dogo-session").textContent);
      if (sd && sd.token) window.__SESSION_DATA__ = sd;
  } catch(e) { window.__SESSION_DATA__ = null; }

  // === 클라우드 구조 ===
  // 별도의 백엔드 서버 없음. Streamlit Python이 발급한 임시 토큰을
  // window.__SESSION_DATA__ 로 주입받고, iframe JS가 OpenAI에 직접 SDP 교환을 한다.
  // (Mixed-content / LAN IP 감지 문제는 사라짐. HTTPS 페이지 → HTTPS api.openai.com 직접 호출)
  console.log("%c[CLOUD MODE] Session token will be embedded by Streamlit. "
      + "iframe talks directly to api.openai.com.", "color:#17a2b8;font-weight:bold");

  const { useState, useEffect, useRef } = React;

  const App = () => {
    const [status, setStatus] = useState({ conn: "IDLE", userSpk: false, aiSpeak: false, firing: false, hit: false, micLevel: 0 });
    // 대화 기록: [{role: 'user'|'ai', text, ts: Date}]
    const [messages, setMessages] = useState([]);
    const [errState, setErrState] = useState({ visible: false, stage: "", model: "", message: "", detail: "" });
    const historyRef = useRef(null);

    useEffect(() => {
        window.updateStatus = (s) => setStatus(p => ({ ...p, ...s }));
        // 기존 호출 형태({user:"..."}, {ai:"..."}) 호환 — 둘 다 append.
        window.updateTranscript = (data) => {
            setMessages(prev => {
                const out = [...prev];
                if (data && data.user) out.push({ role: 'user', text: data.user, ts: new Date() });
                if (data && data.ai)   out.push({ role: 'ai',   text: data.ai,   ts: new Date() });
                return out;
            });
        };
        window.updateError = (e) => setErrState(p => ({ ...p, ...e }));

        // CSS 변수 업데이트 (비행 시간)
        const duration = SETTINGS.missile_duration || 0.6;
        document.documentElement.style.setProperty('--flight-time', duration + 's');

        // [DEBUG] 상태 변경 로그
        console.log(`%c[STATUS CHANGE] Conn: ${status.conn}, UserSpk: ${status.userSpk}`, "color: cyan");
    }, [status.conn, status.userSpk]);

    // 새 메시지가 추가될 때마다 바닥으로 스크롤
    useEffect(() => {
        if (historyRef.current) {
            historyRef.current.scrollTop = historyRef.current.scrollHeight;
        }
    }, [messages.length]);

    const clearHistory = () => setMessages([]);
    const fmtTime = (d) => {
        if (!d) return "";
        const h = String(d.getHours()).padStart(2, '0');
        const m = String(d.getMinutes()).padStart(2, '0');
        return `${h}:${m}`;
    };

    const handleConnect = () => {
        // 새 연결 시 이전 에러는 숨김
        setErrState({ visible: false, stage: "", model: "", message: "", detail: "" });
        setStatus(p => ({ ...p, conn: "CONNECTING..." }));
        window.connectSystem();
    };

    const dismissError = () => setErrState(p => ({ ...p, visible: false }));

    const statusColor =
        status.conn === 'CONNECTED' ? '#28a745' :
        status.conn === 'PAUSED'    ? '#fd7e14' :
        status.conn === 'ERROR'     ? '#dc3545' :
        '#6c757d';

    return (
      <div style={{display:'flex', flexDirection:'column', height:'calc(100vh - 40px)'}}>
        <div className="status-bar">
           <div>AI ENGLISH DOJO | {SETTINGS.level}</div>
           <div style={{color: statusColor}}>{status.conn}</div>
        </div>

        {/* === ERROR BANNER === */}
        {errState.visible && (
          <div className="err-banner">
            <div className="err-title">⚠️ 연결 실패 — AI 음성이 들리지 않는 진짜 원인은 여기에 있습니다</div>
            <div className="err-meta">
              stage: <b>{errState.stage}</b> &nbsp;|&nbsp; model: <b>{errState.model || "(none)"}</b>
            </div>
            <div className="err-detail">{errState.message}{errState.detail ? "\n\n" + errState.detail : ""}</div>
            <div className="err-hint">
              👉 가장 흔한 원인: <b>OpenAI Realtime 모델 ID 만료</b>.<br />
              해결: <code>English/.env</code> 파일에 <code>OPENAI_REALTIME_MODEL=...</code> 를 추가하고 Reconnect 하세요.<br />
              자세한 traceback은 <code>run_app.py</code> 를 띄운 터미널에도 그대로 찍혀 있습니다.
            </div>
            <div style={{marginTop:'8px'}}>
              <button onClick={dismissError} style={{backgroundColor:'#6c757d', padding:'6px 14px'}}>Dismiss</button>
            </div>
          </div>
        )}

        {/* === UI: MISSILE BOX === */}
        <div className={`missile-box ${SETTINGS.is_missile_mode ? 'active' : ''}`}>
           <div className="ninja-icon">
               {status.hit ? '😵' : '🥷'}
           </div>
           
           {/* 미사일 객체 */}
           <div className={`missile-obj ${status.firing ? 'firing' : ''}`}>🚀</div>

           <div className="robot-icon">🤖</div>
           
           <div style={{position:'absolute', bottom:'10px', width:'100%', textAlign:'center', fontSize:'16px', fontWeight:'bold', color: status.hit?'var(--err)':'#666'}}>
               {status.hit ? "HIT! (Responding...)" : (status.firing ? "INCOMING!" : "DEFEND!")}
           </div>
        </div>

        {/* === CONVERSATION HISTORY === */}
        <div className="transcript-box" ref={historyRef}>
            <div style={{display:'flex', alignItems:'center', marginBottom:'8px', position:'sticky', top:0, background:'#fff', paddingBottom:'4px', zIndex:1}}>
                <div style={{flex:1, color:'#6c757d', fontSize:'12px', fontWeight:'bold', letterSpacing:'1px'}}>📢 CONVERSATION HISTORY ({messages.length})</div>
                {messages.length > 0 && (
                    <button className="t-clear-btn" onClick={clearHistory}>Clear</button>
                )}
            </div>
            {messages.length === 0 ? (
                <div className="transcript-empty">
                    {status.conn === "CONNECTED"
                        ? "🎤 지금 말씀해주세요. AI가 듣고 있어요."
                        : "대화를 시작하면 여기에 기록이 쌓입니다."}
                </div>
            ) : (
                messages.map((m, i) => (
                    <div key={i} className={`t-row t-${m.role}`}>
                        <span className="t-label">[{m.role === 'user' ? 'YOU' : 'AI'}]</span>
                        <span className="t-content">{m.text}</span>
                        <span className="t-ts">{fmtTime(m.ts)}</span>
                    </div>
                ))
            )}
            {status.conn === "CONNECTED" && !status.userSpk && messages.length > 0 && (
              <div style={{marginTop:'8px', textAlign:'center', fontSize:'12px', color:'#28a745', fontStyle:'italic'}}>
                🎤 듣고 있어요...
              </div>
            )}
            {status.conn === "PAUSED" && (
              <div style={{marginTop:'8px', textAlign:'center', fontSize:'12px', color:'#fd7e14', fontWeight:'600'}}>
                ⏸ 일시 정지 — Connect 누르면 즉시 재개
              </div>
            )}
        </div>

        <div style={{flex:1}}></div>

        <div style={{textAlign:'center', marginBottom:10}}>
            <div style={{fontSize:12, color:'#666', marginBottom:2}}>MICROPHONE ({status.userSpk ? 'Talking' : 'Silent'})</div>
            <div className="mic-meter"><div className="mic-bar" style={{width: `${Math.min(100, status.micLevel * 2)}%`}}></div></div>
        </div>

        {/* === 🎤 PUSH-TO-TALK BUTTON ===
            클릭: 말하기 시작 (또는 AI 발화 중이면 인터럽트). 끝은 자동 감지(미사일). */}
        <div style={{textAlign:'center', marginBottom:'10px'}}>
           <button
              onClick={() => window.startTalk()}
              disabled={status.conn !== 'CONNECTED'}
              style={{
                 fontSize: '18px',
                 padding: '14px 36px',
                 backgroundColor: status.userSpk ? '#dc3545' : (status.conn==='CONNECTED' ? '#28a745' : '#adb5bd'),
                 color: 'white', border: 'none', borderRadius: '40px',
                 cursor: status.conn === 'CONNECTED' ? 'pointer' : 'not-allowed',
                 fontWeight: 'bold',
                 boxShadow: '0 4px 12px rgba(0,0,0,0.18)',
                 minWidth: '240px',
              }}
              title={status.userSpk ? "듣고 있어요. 말을 멈추면 자동으로 AI에게 보냅니다."
                                    : "클릭 후 말하세요. AI가 말하는 중이면 끊고 차례를 가져옵니다."}
           >
              {status.userSpk
                ? '🎙️ Listening... (멈추면 자동 종료)'
                : (status.aiSpeak
                    ? '✋ Interrupt AI & Talk'
                    : '🎤 Talk to AI')}
           </button>
           <div style={{marginTop:'4px', fontSize:'11px', color:'#6c757d'}}>
              버튼을 누른 후 영어로 말하세요. AI가 말하는 중에 눌러도 됩니다.
           </div>
        </div>

        <div className="button-container">
           <button onClick={handleConnect}
                   disabled={status.conn==='CONNECTED' || status.conn==='CONNECTING...'}>
             {status.conn === 'PAUSED' ? '▶ Resume'
              : status.conn === 'CONNECTED' ? 'Connected'
              : status.conn === 'CONNECTING...' ? 'Connecting...'
              : 'Connect'}
           </button>
           <button onClick={() => window.disconnectSystem()}
                   disabled={status.conn!=='CONNECTED'}>
             ⏸ Pause
           </button>
           <button onClick={() => window.hardDisconnectSystem()}
                   disabled={status.conn==='IDLE'}
                   style={{backgroundColor:'#dc3545'}}>
             🛑 End Session
           </button>
           {/* [LOG DOWNLOAD BUTTON] */}
           <button onClick={() => window.downloadLogs()} style={{backgroundColor:'#6f42c1'}}>📥 Save Logs</button>
        </div>
        <div style={{textAlign:'center', fontSize:'11px', color:'#6c757d', marginBottom:'6px'}}>
          Pause: 일시 정지 (연결 유지) · End Session: 완전 종료. 새 세션은 사이드바 'Reconnect / Apply Settings'.
        </div>
        
        <div className="log-box">Ready. Missile Mode: {SETTINGS.is_missile_mode ? "ON" : "OFF"}</div>
      </div>
    );
  };
  const root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(<App />);
</script>
<script>
// === 🕵️‍♂️ EMERGENCY DEBUGGER (GLOBAL) ===
(function() {
    console.log("%c[DEBUGGER] Global Monitor Started", "color: white; background: red; font-weight: bold");

    // 1. 오디오 데이터 도착 감시 (DataChannel 해킹)
    const observeDataChannel = () => {
        if(window.dc && !window.dc.hasDebugHook) {
            window.dc.hasDebugHook = true;
            const originalOnMessage = window.dc.onmessage;
            window.dc.onmessage = (e) => {
                const ev = JSON.parse(e.data);
                
                // [원인 분석 1] 오디오 데이터가 벌써 오고 있는가?
                if (ev.type === 'response.audio.delta') {
                    if (!window.audioPacketCount) window.audioPacketCount = 0;
                    window.audioPacketCount++;
                    if (window.audioPacketCount % 50 === 0) { // 너무 많으니 50번마다 출력
                        console.log(`%c[INCOMING AUDIO] 🌊 Data Flowing... (Packets: ${window.audioPacketCount})`, "color: cyan");
                    }
                }
                
                // [원인 분석 2] 응답이 두 번 생성되었는가?
                if (ev.type === 'response.created') {
                    console.log(`%c[RESPONSE START] 🎬 New AI Response Started!`, "color: #00ff00; background: #004400; font-weight: bold; border: 2px solid lime");
                    window.audioPacketCount = 0; // 카운터 리셋
                }

                if (originalOnMessage) originalOnMessage(e);
            };
            console.log("%c[DEBUGGER] DataChannel Hooked!", "color: lime");
        }
    };

    // 2. 오디오 플레이어 상태 강제 조회 (0.5초마다)
    setInterval(() => {
        const el = document.getElementById("remoteAudio");
        observeDataChannel(); // DC 연결되면 즉시 훅 설치 시도

        if (el) {
            const isMuted = el.muted;
            const isPaused = el.paused;
            const time = el.currentTime;
            const hasSource = !!el.srcObject;
            
            // 상태가 변하거나 재생 중일 때만 로그
            if (hasSource && (!isPaused || time > 0)) {
                console.log(`%c[PLAYER] Time: ${time.toFixed(2)}s | Paused: ${isPaused} | Muted: ${isMuted} | Vol: ${el.volume}`, 
                    "color: yellow; background: #222");
            }
        }
    }, 500);
})();
</script>
</body>
</html>
"""

def create_realtime_session(settings: Dict[str, Any], target_speed: float = None) -> Dict[str, Any]:
    """OpenAI Realtime GA의 ephemeral client_secret(임시 토큰)을 생성한다.

    클라우드 배포 구조에서는 별도 HTTP 서버 없이 Streamlit Python이 직접
    OpenAI에 토큰을 요청하고, 그 토큰을 iframe HTML에 임베드한다.
    iframe JS는 그 토큰으로 OpenAI(/v1/realtime/calls)와 직접 SDP 교환을 한다.

    Returns:
        성공: {"ok": True, "token": str, "model": str, "instructions": str, "expires_at": int}
        실패: {"ok": False, "stage": str, "error": str, "detail": str, "model": str}
    """
    if not API_KEY:
        return {"ok": False, "stage": "api_key",
                "error": "OPENAI_API_KEY missing in Streamlit secrets.",
                "detail": "", "model": ""}

    # --- 1. 프롬프트 빌드 ---
    try:
        if target_speed is None:
            target_speed = settings.get("target_speed", settings.get("audio_speed", 1.0))
        generated_instructions = build_instructions_from_dict(settings, target_speed)
    except Exception as e:
        return {"ok": False, "stage": "build_prompt",
                "error": f"Prompt build failed: {e}",
                "detail": traceback.format_exc(), "model": ""}

    # --- 2. 모델 후보 결정 (secrets/env override + 폴백) ---
    env_model = (ENV_REALTIME_MODEL or "").strip()
    client_model = str(settings.get("model", "") or "").strip()
    fallback_models = [
        "gpt-realtime",
        "gpt-realtime-2025-08-28",
        "gpt-realtime-2",
        "gpt-realtime-1.5",
        "gpt-4o-realtime-preview-2025-06-03",
        "gpt-4o-realtime-preview",
        "gpt-4o-realtime-preview-2024-12-17",
        "gpt-4o-mini-realtime-preview",
        "gpt-4o-mini-realtime-preview-2024-12-17",
    ]
    ordered = []
    for m in [env_model, client_model] + fallback_models:
        if m and m not in ordered:
            ordered.append(m)

    # --- 3. Voice / Audio / Length ---
    env_voice = (ENV_REALTIME_VOICE or "").strip()
    client_voice = str(settings.get("voice", "") or "").strip()
    voice = env_voice or client_voice or "alloy"

    audio_input = {"transcription": {"model": "whisper-1"}}
    if settings.get("is_missile_mode", False):
        audio_input["turn_detection"] = None
    else:
        audio_input["turn_detection"] = {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 500,
        }

    response_length_raw = str(settings.get("response_length", "Medium")).strip().capitalize()
    max_tokens_map = {"Short": 200, "Medium": 800, "Long": 3500}
    max_resp_tokens = max_tokens_map.get(response_length_raw, 400)

    print(f"\n=== create_realtime_session (GA) ===")
    print(f"[LEVEL] {settings.get('level', 'Unknown')}")
    print(f"[MODE] {settings.get('mode', 'Unknown')}")
    print(f"[CORRECTION_STYLE] {settings.get('correction_style', 'Minor')}")
    print(f"[RESPONSE_LENGTH] {response_length_raw} (max_tokens={max_resp_tokens})")
    print(f"[MISSILE_MODE] {settings.get('is_missile_mode', False)}")
    print(f"[MODEL CANDIDATES] {ordered}")
    print(f"[VOICE] {voice}")

    def build_ga_session(model_name: str) -> dict:
        return {
            "session": {
                "type": "realtime",
                "model": model_name,
                "instructions": generated_instructions,
                "output_modalities": ["audio"],
                "audio": {
                    "input": audio_input,
                    "output": {"voice": voice},
                },
                "max_response_output_tokens": max_resp_tokens,
            }
        }

    session_url = "https://api.openai.com/v1/realtime/client_secrets"
    session_headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    def _read_http_error_body(http_err: urllib.error.HTTPError) -> str:
        try:
            raw = http_err.read()
            return raw.decode('utf-8', errors='replace') if raw else ""
        except Exception:
            return ""

    def _post_session(payload: dict) -> dict:
        req = urllib.request.Request(
            session_url,
            data=json.dumps(payload).encode('utf-8'),
            headers=session_headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _extract(body: dict) -> tuple:
        token = body.get('value') or body.get('client_secret', {}).get('value')
        expires_at = body.get('expires_at') or body.get('client_secret', {}).get('expires_at') or 0
        return token, expires_at

    last_error = ""
    for candidate in ordered:
        payload = build_ga_session(candidate)
        try:
            print(f"[SESSION] Trying model: {candidate}")
            body = _post_session(payload)
            token, expires_at = _extract(body)
            if not token:
                last_error = f"No token in response: {body}"
                continue
            print(f"[SESSION] ✅ Success with model: {candidate}")
            return {"ok": True, "token": token, "model": candidate,
                    "instructions": generated_instructions, "expires_at": expires_at}
        except urllib.error.HTTPError as he:
            body_text = _read_http_error_body(he)
            last_error = f"HTTP {he.code} {he.reason}: {body_text}"
            print(f"[SESSION] ❌ {candidate} failed -> {last_error}")
            if he.code in (401, 403):
                return {"ok": False, "stage": "session_auth",
                        "error": f"OpenAI auth failed ({he.code} {he.reason}). Check OPENAI_API_KEY.",
                        "detail": last_error, "model": candidate}
            # max_response_output_tokens 거부 시 한 번 더 재시도
            if "max_response_output_tokens" in (body_text or ""):
                print("[SESSION] ⚠️ retrying without max_response_output_tokens")
                try:
                    payload_no_max = build_ga_session(candidate)
                    payload_no_max["session"].pop("max_response_output_tokens", None)
                    body2 = _post_session(payload_no_max)
                    token, expires_at = _extract(body2)
                    if token:
                        print(f"[SESSION] ✅ Success with model: {candidate} (no max_tokens)")
                        return {"ok": True, "token": token, "model": candidate,
                                "instructions": generated_instructions, "expires_at": expires_at}
                except Exception as retry_err:
                    last_error = f"retry without max_tokens failed: {retry_err}"
            continue
        except urllib.error.URLError as ue:
            last_error = f"URLError: {ue}"
            print(f"[SESSION] ❌ {candidate} URLError -> {ue}")
            continue
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"[SESSION] ❌ {candidate} unexpected -> {e}")
            continue

    return {
        "ok": False,
        "stage": "session_create",
        "error": "All model candidates failed at /v1/realtime/client_secrets. "
                 "Set OPENAI_REALTIME_MODEL in secrets to a valid model (e.g. gpt-realtime).",
        "detail": last_error,
        "model": ",".join(ordered),
    }


require_password_gate()

st.title("AI English Dojo")
if not API_KEY:
    st.error("⚠️ OPENAI_API_KEY가 설정되지 않았습니다. Streamlit Cloud의 Secrets에 OPENAI_API_KEY를 추가하세요.")
    st.stop()

with st.sidebar:
    st.header("Settings")
    
    # [3단계] 레벨 변경 시 자동 재연결 트리거
    if 'trigger_reconnect' not in st.session_state:
        st.session_state.trigger_reconnect = False
    if 'previous_level' not in st.session_state:
        st.session_state.previous_level = None
    
    def on_level_change():
        """레벨 변경 시 target_speed를 즉시 업데이트하고 재연결 트리거"""
        new_level = st.session_state.get('level_select', 'Intermediate (중급)')
        old_level = st.session_state.get('previous_level')
        new_speed = update_target_speed(new_level)
        st.session_state.updated = time.time()
        
        # 레벨이 실제로 변경되었으면 재연결 트리거
        if old_level is not None and old_level != new_level:
            st.session_state.trigger_reconnect = True
            print(f"[LEVEL CHANGE] {old_level} -> {new_level}, Speed: {new_speed}x, RECONNECT TRIGGERED!")
        else:
            print(f"[LEVEL INIT] Level: {new_level}, Speed: {new_speed}x")
        
        st.session_state.previous_level = new_level
    
    def update(): 
        st.session_state.updated = time.time()
        # 현재 레벨에 맞게 target_speed도 동기화
        current_level = st.session_state.get('level_select', 'Intermediate (중급)')
        initialize_target_speed(current_level)
        
    level = st.selectbox(
        "Level", 
        [l.value for l in UserLevel], 
        index=2, 
        on_change=on_level_change,
        key='level_select'
    )
    
    # 레벨 선택 후 target_speed 동기화 및 이전 레벨 저장
    initialize_target_speed(level)
    if st.session_state.previous_level is None:
        st.session_state.previous_level = level
    
    topic = st.selectbox("Topic", ["일상 수다 (Daily)", "여행 (Travel)", "비즈니스 (Business)", "식당 주문 (Ordering)"], on_change=update)
    mode = st.selectbox("Mode", [m.value for m in ChatMode], on_change=update)

    # ===============================================
    # 교정 강도 (Correction Style) - CORRECTION_CHAT, SPARTA 모드에서만 노출
    # ===============================================
    correction_style = "Minor"  # 기본값
    if mode in (ChatMode.CORRECTION_CHAT.value, ChatMode.SPARTA.value):
        correction_style = st.radio(
            "Correction Style (교정 강도)",
            ["Minor", "Major"],
            index=0,
            horizontal=True,
            help=(
                "• Minor: 내가 말한 표현을 최대한 살리고 문법/시제/어순만 살짝 교정\n"
                "• Major: 같은 의미지만 더 정확하고 자연스러운 표현으로 다시 써줌"
            ),
            on_change=update,
            key='correction_style_select'
        )
        if correction_style == "Minor":
            st.caption("🪶 Minor: 내가 한 말 중심으로 최소 교정")
        else:
            st.caption("🛠️ Major: 더 정확한 표현으로 재작성")

    # ===============================================
    # 응답 길이 (Response Length) - SPARTA가 아닐 때만 노출
    # SPARTA는 교정만 echo하므로 응답 길이 개념이 없음.
    # ===============================================
    response_length = "Medium"  # 기본값
    if mode != ChatMode.SPARTA.value:
        response_length = st.radio(
            "Response Length (응답 길이)",
            ["Short", "Medium", "Long"],
            index=1,  # 기본 Medium
            horizontal=True,
            help=(
                "• Short  : 1문장 + 1질문 (~10~15 단어). 빠른 반응 중심.\n"
                "• Medium: 2~3문장 + 1질문 (~25~40 단어). 가벼운 코멘트 + 질문.\n"
                "• Long  : 4~6문장 + 1질문 (~60~100 단어). 개인적 반응 + 관련 정보 + 질문."
            ),
            on_change=update,
            key='response_length_select'
        )
        len_caption = {
            "Short":  "⚡ Short — 짧고 빠른 반응",
            "Medium": "💬 Medium — 적당한 길이의 대화",
            "Long":   "📖 Long — 풍부한 코멘트 + 깊이 있는 질문",
        }.get(response_length, "")
        if len_caption:
            st.caption(len_caption)

    st.divider()
    missile = st.checkbox("Missile Mode", False, on_change=update)
    missile_duration = 0.6
    if missile:
        missile_duration = st.slider("Missile Speed (sec)", 0.1, 3.0, 0.6, 0.1, on_change=update)
    if st.button("Reconnect / Apply Settings"): 
        st.session_state.trigger_reconnect = True
        update()

# st.session_state.target_speed를 컨트롤 타워로 사용
current_target_speed = get_target_speed()

# [3단계] 재연결 트리거 확인
trigger_reconnect = st.session_state.get('trigger_reconnect', False)

client_config = {
    "level": level,
    "topic": topic,
    "mode": mode,
    "correction_style": correction_style,  # Minor / Major
    "response_length": response_length,    # Short / Medium / Long
    "is_missile_mode": missile,
    "missile_duration": missile_duration,
    "audio_speed": current_target_speed,  # Deprecated (브라우저 playbackRate용)
    "target_speed": current_target_speed,  # Deprecated
    "voice_speed": get_voice_speed_from_level(level),  # ★ Realtime API voice speed (0.6 ~ 1.2)
    "session_update_required": st.session_state.get('session_update_required', False),
    "trigger_reconnect": trigger_reconnect,  # [3단계] 재연결 트리거
    # ★ api_key 는 더 이상 브라우저에 노출하지 않는다.
    #   Streamlit Python 이 ephemeral token 만 생성해서 __SESSION_JSON__ 으로 주입한다.
    "__update_token__": str(time.time())  # Force HTML/JS reload on setting change
}

# 플래그 리셋
if st.session_state.get('session_update_required', False):
    st.session_state.session_update_required = False
if trigger_reconnect:
    st.session_state.trigger_reconnect = False

# ============================================================
# Session lifecycle
# trigger_reconnect (=Apply Settings 클릭) 시 OpenAI ephemeral token을 새로 발급
# st.session_state.active_session 에 저장 → iframe에 주입
# 토큰은 보통 60초 유효. 만료 시 다시 Apply Settings 누르면 됨.
# ============================================================
if trigger_reconnect:
    with st.spinner("🔑 OpenAI 세션 토큰을 발급받는 중..."):
        result = create_realtime_session(client_config, current_target_speed)
    if result.get("ok"):
        st.session_state.active_session = {
            "token": result["token"],
            "model": result["model"],
            "instructions": result["instructions"],
            "expires_at": result.get("expires_at", 0),
            "created_at": time.time(),
        }
    else:
        st.session_state.active_session = None
        st.session_state.last_session_error = result
        st.error(
            f"세션 생성 실패 [{result.get('stage')}] "
            f"(model: {result.get('model')})\n\n{result.get('error')}"
        )
        if result.get("detail"):
            with st.expander("자세한 오류"):
                st.code(result["detail"])

active_session = st.session_state.get("active_session")

# [1단계 Python 로그] 상시 디버그 정보 표시
st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 Debug Panel (1단계)")

# 실시간 상태 표시
debug_col1, debug_col2 = st.sidebar.columns(2)
with debug_col1:
    st.metric("Voice Speed", f"{client_config['voice_speed']}x")
with debug_col2:
    st.metric("Level", level.split(" ")[0])

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎛️ Voice Speed Control Tower")
st.sidebar.code(f"""
[Realtime API Voice Speed]
voice_speed: {client_config['voice_speed']}x
level: {level}
trigger_reconnect: {trigger_reconnect}

[Speed Mapping]
왕초보: 0.7x (slow)
초급: 0.8x (slightly slow)
중급: 0.9x (near normal)
고급: 1.0x (normal)
""")

st.sidebar.markdown("**Realtime API Voice Speed:**")
voice_speed = client_config['voice_speed']
if voice_speed == 0.7:
    st.sidebar.success("🐢 느림 (70%) - 왕초보")
elif voice_speed == 0.8:
    st.sidebar.success("🐌 약간 느림 (80%) - 초급")
elif voice_speed == 0.9:
    st.sidebar.info("🚶 거의 보통 (90%) - 중급")
elif voice_speed == 1.0:
    st.sidebar.info("🏃 보통 (100%) - 고급")

# 속도 변경 이력 표시
if st.session_state.get('speed_changed_at'):
    st.sidebar.caption(f"⏱️ Last speed change: {time.strftime('%H:%M:%S', time.localtime(st.session_state.speed_changed_at))}")

st.sidebar.info("💡 **Question-Driven Dialogue:**\n1. AI의 모든 응답이 질문으로 끝남 (강제)\n2. 후속 질문으로 자연스러운 대화 유도\n3. 모든 레벨에서 적용되는 대화 연습\n4. Voice Speed: 레벨별 자동 조정 (0.7~1.0x)")

# ============================================================
# Render iframe only when we have an active session token
# ============================================================
if not active_session:
    st.info(
        "👈 사이드바에서 설정을 선택하고 **'Reconnect / Apply Settings'** 버튼을 눌러 세션을 시작하세요.\n\n"
        "- 버튼을 누르면 OpenAI에 임시 세션 토큰을 요청합니다.\n"
        "- 토큰을 받으면 아래에 대화창이 나타납니다.\n"
        "- 토큰은 약 60초간 유효하며, 만료되면 다시 버튼을 누르면 됩니다."
    )
else:
    # 만료 임박 안내 (참고용)
    created_at = active_session.get("created_at", 0)
    expires_at = active_session.get("expires_at", 0)
    if expires_at and expires_at > 0:
        remaining = int(expires_at - time.time())
        if remaining > 0:
            st.caption(f"🔑 세션 토큰 유효 (~{remaining}초). 만료 후엔 'Reconnect / Apply Settings' 다시 누르세요.")
        else:
            st.warning("⏳ 세션 토큰이 만료되었을 수 있습니다. 'Reconnect / Apply Settings'를 다시 눌러주세요.")

    # 보안: instructions, token 은 브라우저로 들어가지만, 토큰은 1분짜리 ephemeral 이라 안전.
    session_payload = {
        "token": active_session["token"],
        "model": active_session["model"],
        # instructions 는 디버그용으로만 노출 (실제 동작에는 영향 없음. 원하면 제거 가능)
        "instructions_preview": (active_session.get("instructions") or "")[:200],
        "created_at": active_session.get("created_at", 0),
        "expires_at": active_session.get("expires_at", 0),
    }

    html = (
        REALTIME_CLIENT_HTML_TEMPLATE
        .replace("__SETTINGS_JSON__", json.dumps(client_config))
        .replace("__SESSION_JSON__",  json.dumps(session_payload))
    )
    components.html(html, height=900, scrolling=False)
