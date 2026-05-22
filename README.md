# AI English Dojo

OpenAI Realtime API 기반의 음성 영어 학습 앱 (Streamlit).

- 마이크로 영어 회화 → AI가 교정 + 응답
- 레벨/주제/모드/교정 강도(Minor/Major)/응답 길이(Short/Medium/Long) 선택
- 미사일 모드(특정 시간 동안 사용자가 말한 후 즉시 교정) 지원
- HTTPS 자동 → 핸드폰 Chrome / Safari / 삼성인터넷 어디서나 동작

---

## 구조

PC 없이 핸드폰만으로도 동작하도록 Streamlit Cloud에 배포하는 단일 서비스 구조입니다.

```
[사용자 브라우저]
    │
    │ (HTTPS)
    ▼
[Streamlit Cloud: app.py]
    │ 1) UI 렌더링
    │ 2) OpenAI에 ephemeral 토큰 요청 (서버측, API 키 보호)
    │ 3) 토큰을 iframe HTML에 주입
    ▼
[iframe JS]
    │ 4) WebRTC SDP 교환을 OpenAI에 직접 (ephemeral 토큰만 사용)
    ▼
[api.openai.com/v1/realtime]
```

---

## 배포 방법 (Streamlit Cloud, 무료)

### 1. GitHub 저장소 생성

1. github.com 에서 새 **public** 저장소 생성 (예: `ai-english-dojo`)
2. 로컬에서 이 폴더로 이동 후:

   ```bash
   git init
   git add .
   git commit -m "Initial commit: cloud-ready"
   git remote add origin https://github.com/<your-username>/ai-english-dojo.git
   git branch -M main
   git push -u origin main
   ```

   `.gitignore`가 `.streamlit/secrets.toml`, `run_app.py`, 로그 파일 등을 자동 제외합니다.

### 2. Streamlit Cloud 연결

1. https://share.streamlit.io 접속 → 우측 상단 **Sign in** → GitHub 계정으로 로그인
2. **New app** 클릭
3. 입력값:
   - Repository: `<your-username>/ai-english-dojo`
   - Branch: `main`
   - Main file path: `app.py`
4. **Advanced settings** → **Secrets** 클릭, 아래 내용 붙여넣기 (값은 본인 것으로):

   ```toml
   OPENAI_API_KEY = "sk-..."
   APP_PASSWORD = "본인이 정한 접속 비밀번호"
   # OPENAI_REALTIME_MODEL = "gpt-realtime"   # (선택)
   # OPENAI_REALTIME_VOICE = "alloy"          # (선택)
   ```

5. **Deploy!** 클릭
6. 1~2분 후 `https://<your-app-name>.streamlit.app` 주소가 발급됨

### 3. 접속 및 사용

1. 핸드폰/PC 어디서든 발급된 URL 접속
2. 비밀번호 입력 → 메인 화면
3. 사이드바에서 설정 선택 → **Reconnect / Apply Settings** 클릭
4. iframe이 나타나면 안쪽의 **Connect** 버튼을 눌러 마이크 권한 허용

> 모바일에서 처음 접속 시: Chrome / Safari 모두 정상 동작 (HTTPS).
> `chrome://flags` 같은 우회는 더 이상 필요 없음.

---

## 비용 보호

- OpenAI 대시보드 (https://platform.openai.com/settings/organization/billing) 에서
  **월 사용량 한도(Hard Limit)** 를 반드시 설정하세요. 예: $20/월.
- `APP_PASSWORD` 가 노출되지 않도록 주의.
- 비밀번호가 노출되었다고 판단되면 Secrets 에서 즉시 변경.

---

## 로컬 개발

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# secrets.toml 에 본인 API 키와 비밀번호 입력 (커밋 금지)
python run_app.py
# 또는: streamlit run app.py
```

브라우저에서 http://localhost:8503 접속.

---

## 파일 구성

```
English/
├── app.py                          # 메인 앱 (Streamlit + iframe)
├── requirements.txt                # 의존성 (streamlit 만 필요)
├── run_app.py                      # 로컬 실행 편의 (클라우드 배포에는 무관)
├── README.md                       # 이 파일
├── .gitignore                      # secrets.toml 등 제외
└── .streamlit/
    ├── config.toml                 # 기본 Streamlit 설정
    └── secrets.toml.example        # 비밀값 템플릿 (실제 secrets.toml 은 커밋 금지)
```

---

## 트러블슈팅

- **"OPENAI_API_KEY가 설정되지 않았습니다"**: Streamlit Cloud Secrets 에 키가 비었거나
  키 이름이 정확히 `OPENAI_API_KEY` 가 아님.
- **"세션 토큰이 만료되었습니다"**: 60초 ephemeral 한도 초과. 사이드바 버튼을 다시 눌러 새 토큰 발급.
- **iframe 안에서 Connect 눌렀는데 무반응**: 브라우저 콘솔(F12) 로그 확인.
  `[CONNECT ERROR]` 로그가 있으면 토큰/모델/상세 메시지가 보임.
- **모델 폴백 다 실패**: OpenAI 계정에서 Realtime 모델 접근 권한 확인. Secrets 의
  `OPENAI_REALTIME_MODEL` 을 `gpt-realtime` 으로 고정해 보세요.
