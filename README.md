# 🎮 Discord Minecraft Server Manager Bot

Docker와 Discord API를 활용하여 디스코드 내에서 마인크래프트 서버를 완벽하게 통제하고 모니터링할 수 있는 강력한 서버 관리 봇입니다. 

단순한 서버 온/오프를 넘어, 실시간 자원(CPU/RAM) 모니터링, 포트 충돌 방지, 그리고 **Modrinth API를 활용한 플러그인/모드 자동 설치**까지 지원합니다.

## ✨ 주요 기능 (Features)

* 🖥️ **통합 모니터링 대시보드 (Live Dashboard)**
  * 30초 주기로 갱신되는 실시간 상태 패널 제공
  * 서버별 접속자 수(`mcstatus`) 및 호스트 자원 점유율(CPU, RAM) 실시간 표시
  * 봇이 재시작되어도 기존 대시보드 UI 완벽 복구 (상태 저장)
* 🏗️ **UI 기반 서버 생성 (Server Creation UI)**
  * 디스코드 팝업(Modal) 창을 통한 손쉬운 서버 배포
  * Paper, Arclight, Fabric 등 구동기 타입 선택 지원
  * 2G ~ 14G 까지 세밀한 JVM 메모리 할당
  * **단일 서버 활성 보장:** 포트(25565) 충돌 방지를 위해 기존 서버 자동 안전 종료 후 새 서버 구동
* 🎛️ **원격 서버 제어 (Remote Control Panel)**
  * 버튼 클릭 한 번으로 서버 시작, 종료, 재시작 및 영구 삭제(데이터 폴더 포함) 지원
* 🧩 **플러그인 및 모드 자동 설치 (Modrinth API Integration)**
  * 디스코드 내에서 Modrinth 생태계의 모드/플러그인 직접 검색
  * 드롭다운 메뉴로 선택 시 컨테이너 볼륨(`plugins` 또는 `mods`)으로 `.jar` 파일 자동 다운로드 및 라우팅 (하이브리드 서버 완벽 호환)
* 🛡️ **철저한 보안 (Security)**
  * 모든 서버 제어 버튼 및 명령어는 **디스코드 서버 관리자(Administrator)** 권한 보유자만 사용 가능

---

## 🛠️ 기술 스택 (Tech Stack)

* **Language:** Python 3.10+
* **Libraries:** `discord.py`, `docker`, `mcstatus`, `asyncio`, `urllib`
* **Infrastructure:** Docker (`itzg/minecraft-server` image)
* **Package Manager:** `uv` (권장) 또는 `pip`

---

## 🚀 설치 및 실행 방법 (Getting Started)

### 1. 사전 준비 (Prerequisites)
* 호스트 머신에 **Docker**가 설치되어 있고 데몬이 실행 중이어야 합니다.
* 디스코드 개발자 포털에서 봇을 생성하고 **토큰(Token)**을 발급받아야 합니다.
* 봇의 `Message Content Intent`가 활성화되어 있어야 합니다.

### 2. 환경 변수 설정
프로젝트 루트 디렉토리에 `.env` 파일을 생성하고 발급받은 디스코드 봇 토큰을 입력합니다.
```env
DISCORD_TOKEN=your_discord_bot_token_here
```

### 3. 패키지 설치
```bash
uv venv
uv pip install discord.py docker mcstatus python-dotenv
```

### 4. 봇 실행
```bash
uv run main.py
```

## 📚 명령어 가이드 (Commands)

> ⚠️ 아래 명령어 중 상태 확인을 제외한 모든 조작은 **관리자 권한**이 필요합니다.

### 📊 일반/모니터링 명령어
* `!상태` : 봇과 Docker 데몬의 연결 상태를 확인합니다.
* `!목록` : 현재 생성된 모든 마인크래프트 서버 컨테이너 목록을 출력합니다.
* `!도움말` : 봇의 사용 방법과 명령어 목록을 안내합니다.
* **`!모니터링시작` (⭐추천)** : 현재 채널에 실시간으로 상태가 갱신되는 통합 관리 UI 대시보드를 생성합니다.

### ⚙️ 서버 제어 명령어
* `!서버생성 [이름] [버전] [메모리] [타입]` : 새로운 마인크래프트 서버를 생성합니다.
  * 예: `!서버생성 생존서버 1.20.4 8G PAPER`
* `!서버종료 [서버이름]` : 실행 중인 서버의 월드를 안전하게 저장하고 종료합니다.
* `!서버삭제 [서버이름]` : 컨테이너 및 연관된 월드 데이터(호스트 폴더)를 영구 삭제합니다.
* `!제어판 [서버이름]` : 특정 서버를 개별 관리할 수 있는 버튼 뷰를 출력합니다.

### 🧩 플러그인/모드 관리
* `!플러그인검색 [서버이름] [검색어]` (또는 `!모드검색`) : Modrinth에서 플러그인/모드를 검색하고 드롭다운을 통해 서버에 즉시 설치합니다.

---

## 📁 디렉토리 구조 (Directory Structure)

서버가 생성되면 프로젝트 루트의 `./mc_data` 폴더 아래에 서버 이름으로 볼륨이 마운트됩니다. 백업이나 설정 파일 수정 시 이 폴더를 직접 제어할 수 있습니다.

```text
📦 Minecraft-Bot-Project
 ┣ 📂 mc_data                  # 서버들의 월드 및 설정 파일이 저장되는 호스트 볼륨 경로
 ┃ ┣ 📂 survival-1             # 생성된 서버 이름
 ┃ ┃ ┣ 📂 plugins (or mods)    # Modrinth API를 통해 다운로드된 파일이 저장되는 곳
 ┃ ┃ ┣ 📜 server.properties
 ┃ ┃ ┗ 📂 world
 ┣ 📜 bot.py                   # 봇 메인 소스 코드
 ┣ 📜 dashboard_data.json      # 봇 재시작 시 대시보드 복구를 위한 상태 저장 파일
 ┣ 📜 .env                     # 환경 변수 (토큰)
 ┗ 📜 README.md
```

## ⚠️ 트러블슈팅 (FAQ & Troubleshooting)

**Q. `[ERROR] Failed to locate Arclight jar for ... from latest` 오류가 발생합니다.**
> Arclight나 Forge 등 하이브리드/모드 서버 구동기는 최신 마인크래프트 버전 배포가 늦습니다. 버전 입력 시 `LATEST`를 사용하지 말고 `1.20.4` 등 안정화된 버전을 직접 명시하여 서버를 생성해 주세요.

**Q. 콘솔에 `429 Too Many Requests` 에러가 뜹니다.**
> 모니터링 대시보드가 너무 자주 갱신되어 디스코드 API 제한에 걸린 것입니다. 백그라운드에 켜져 있는 다른 봇 프로세스(좀비 프로세스)가 없는지 확인하고, 봇 프로세스를 모두 강제 종료한 뒤 1개만 다시 실행해 주세요.

**Q. 디스코드에서 버튼을 눌렀는데 `상호작용 실패`라고 뜹니다.**
> 봇이 재시작되기 전에 생성되었던 메시지일 수 있습니다. `dashboard_data.json`이 정상적으로 저장되고 있는지 확인하시거나, 기존 메시지를 삭제하고 `!모니터링시작`을 다시 입력해 새 패널을 띄워주세요.