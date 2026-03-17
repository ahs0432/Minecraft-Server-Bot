import discord
from discord.ext import commands, tasks
import docker
import os
import shutil  
from dotenv import load_dotenv
from mcstatus import JavaServer
import json
import asyncio

import urllib.request
import urllib.parse

import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# --- Phase 13: 로깅 설정 ---
# 로그 포맷: [2026-03-17 22:24:24] [INFO] [유저명]: 메시지
log_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# 파일 저장 설정 (최대 5MB씩 5개까지 보관 후 순환)
log_handler = RotatingFileHandler('bot.log', maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
log_handler.setFormatter(log_formatter)

# 콘솔 출력 설정
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# 로거 구성
logger = logging.getLogger('MinecraftBot')
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logger.addHandler(console_handler)

# --- 상호작용 기록용 도우미 함수 ---
def log_interaction(user, command, target=None, result="Success"):
    log_msg = f"[{user}] 실행: !{command}"
    if target:
        log_msg += f" (대상: {target})"
    log_msg += f" | 결과: {result}"
    logger.info(log_msg)

# 환경 변수 및 디스코드 권한 셋팅
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# 환경 변수에 따라 Docker 또는 Podman 소켓 자동 선택
def get_docker_client():
    try:
        # 1. 먼저 표준 환경변수 확인
        return docker.from_env()
    except:
        # 2. 실패 시 Podman 사용자 소켓 시도 (Linux 기준)
        if os.name != 'nt': # 리눅스/맥인 경우
            uid = os.getuid()
            podman_sock = f"unix:///run/user/{uid}/podman/podman.sock"
            if os.path.exists(podman_sock.replace("unix://", "")):
                return docker.DockerClient(base_url=podman_sock)
        raise Exception("Docker 또는 Podman 소켓을 찾을 수 없습니다.")

docker_client = get_docker_client()

# --- 봇 시작 시 View 및 대시보드 복구 설정 ---
async def setup_hook():
    # 1. 기존 대시보드 UI 복구
    if docker_client:
        containers = docker_client.containers.list(all=True, filters={"ancestor": "itzg/minecraft-server"})
        bot.add_view(DashboardView(containers))
        
        # 2. 개별 서버 제어 버튼(View)들 복구
        for c in containers:
            bot.add_view(ServerControlView(c.name))

bot.setup_hook = setup_hook

@bot.event
async def on_ready():
    print('====================================')
    print(f'봇 로그인 성공: {bot.user.name}')
    print('====================================')
    
    # 봇이 켜질 때 JSON 파일에서 대시보드 위치 읽어오기
    load_dashboard_data()
    
    # 저장된 대시보드 정보가 있다면 자동 갱신 루프 재시작
    if DASHBOARD_CHANNEL_ID and DASHBOARD_MESSAGE_ID and not update_dashboard.is_running():
        update_dashboard.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ **권한 오류:** 이 명령어를 사용할 수 있는 관리자 권한이 없습니다.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ **명령어 입력 오류:** 필수 입력값(`{error.param.name}`)이 누락되었습니다.\n💡 사용법을 확인하려면 `!도움말`을 입력해주세요.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"❌ 알 수 없는 오류가 발생했습니다: {error}")

# --- Phase 13-1: 모든 명령어 입력 자동 로깅 ---
@bot.event
async def on_command(ctx):
    """유저가 명령어를 입력하면 실행 직전에 로그를 남깁니다."""
    # !서버생성 야생 1.20.4 같이 입력된 전체 인자값 파악
    args = ", ".join(ctx.args[1:]) if len(ctx.args) > 1 else "없음"
    
    # 로그 기록
    log_msg = f"[{ctx.author}] 명령어 사용: !{ctx.command.name} | 인자: ({args})"
    logger.info(log_msg)

@bot.event
async def on_command_error(ctx, error):
    """명령어 실행 중 오류가 발생한 경우 로그를 남깁니다."""
    if isinstance(error, commands.MissingPermissions):
        logger.warning(f"[{ctx.author}] 권한 부족 거부: !{ctx.command.name}")
    elif isinstance(error, commands.CommandNotFound):
        # 존재하지 않는 명령어는 무시하거나 기록할 수 있습니다.
        pass
    else:
        logger.error(f"[{ctx.author}] 명령어 오류 (!{ctx.command.name}): {error}")

# --- 대시보드 상태 저장용 변수 및 함수 ---
DASHBOARD_DATA_FILE = "dashboard_data.json"
DASHBOARD_CHANNEL_ID = None
DASHBOARD_MESSAGE_ID = None

def load_dashboard_data():
    global DASHBOARD_CHANNEL_ID, DASHBOARD_MESSAGE_ID
    if os.path.exists(DASHBOARD_DATA_FILE):
        with open(DASHBOARD_DATA_FILE, "r") as f:
            data = json.load(f)
            DASHBOARD_CHANNEL_ID = data.get("channel_id")
            DASHBOARD_MESSAGE_ID = data.get("message_id")

def save_dashboard_data():
    with open(DASHBOARD_DATA_FILE, "w") as f:
        json.dump({
            "channel_id": DASHBOARD_CHANNEL_ID,
            "message_id": DASHBOARD_MESSAGE_ID
        }, f)


def fetch_modrinth_search(query: str):
    """Modrinth에서 플러그인/모드를 검색하여 상위 10개 결과를 반환합니다."""
    url = f"https://api.modrinth.com/v2/search?query={urllib.parse.quote(query)}&limit=10"
    req = urllib.request.Request(url, headers={'User-Agent': 'DiscordBot-MinecraftManager/1.0'})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return data.get('hits', [])
    except Exception as e:
        print(f"Modrinth Search Error: {e}")
        return []

def get_modrinth_download_url(project_id: str):
    """특정 프로젝트의 최신 다운로드 파일 URL과 이름을 반환합니다."""
    url = f"https://api.modrinth.com/v2/project/{project_id}/version"
    req = urllib.request.Request(url, headers={'User-Agent': 'DiscordBot-MinecraftManager/1.0'})
    try:
        with urllib.request.urlopen(req) as response:
            versions = json.loads(response.read().decode())
            for v in versions:
                if v.get('files'):
                    # 첫 번째(최신) 파일의 다운로드 URL과 파일명 반환
                    return v['files'][0]['url'], v['files'][0]['filename']
    except Exception as e:
        print(f"Modrinth Download Error: {e}")
    return None, None

def get_latest_mc_version():
    """Mojang API를 호출하여 현재 최신 정식(Release) 버전을 반환합니다."""
    url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DiscordBot-MinecraftManager/1.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return data['latest']['release']  # 예: "1.21.1"
    except Exception as e:
        print(f"Mojang API Error: {e}")
        return "latest" # 통신 실패 시 기본값 폴백
    
# --- Phase 11 수정: 완벽한 자동 분류를 지원하는 드롭다운 컴포넌트 ---
class PluginSelect(discord.ui.Select):
    def __init__(self, server_name: str, server_type: str, hits: list):
        self.server_name = server_name
        self.server_type = server_type
        
        options = []
        for hit in hits:
            desc = hit.get('description', '')[:90] + "..." if len(hit.get('description', '')) > 90 else hit.get('description', '')
            p_type = hit.get('project_type', 'mod') # 모드인지 플러그인인지 확인
            
            # 아이콘을 모드(📦)와 플러그인(🧩)으로 구분하여 시각적 효과 추가
            emoji = "📦" if p_type == "mod" else "🧩"
            
            options.append(discord.SelectOption(
                label=hit['title'][:25], 
                description=f"[{p_type.upper()}] {desc}", 
                # value에 프로젝트 ID와 타입을 함께 저장하여 콜백으로 넘김
                value=f"{hit['project_id']}|{p_type}",
                emoji=emoji
            ))
            
        if not options:
            options.append(discord.SelectOption(label="검색 결과가 없습니다.", value="none"))
            
        super().__init__(placeholder="⬇️ 설치할 항목을 선택하세요...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("설치할 항목이 없습니다.", ephemeral=True)
            return
            
        # 넘겨받은 value에서 ID와 타입을 분리
        project_id, project_type = self.values[0].split("|")
        selected_label = [opt.label for opt in self.options if opt.value == self.values[0]][0]
        
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(f"⏳ `{selected_label}` 다운로드를 준비 중입니다...", ephemeral=True)
        
        try:
            url, filename = await asyncio.to_thread(get_modrinth_download_url, project_id)
            if not url:
                await interaction.followup.send("⚠️ 해당 프로젝트의 다운로드 파일을 찾을 수 없습니다.", ephemeral=True)
                return
            
            # --- 💡 완벽하게 개선된 타겟 디렉토리(폴더) 분류 로직 ---
            target_dir = "plugins" # 기본값
            
            if self.server_type == "FABRIC":
                target_dir = "mods" # 패브릭은 무조건 모드 폴더
            elif self.server_type == "PAPER":
                target_dir = "plugins" # 페이퍼는 무조건 플러그인 폴더
            elif self.server_type == "ARCLIGHT":
                # 하이브리드인 아크라이트는 Modrinth가 알려준 원본 타입에 따라 스마트하게 분류!
                target_dir = "mods" if project_type == "mod" else "plugins"
                
            host_dir = os.path.abspath(f"./mc_data/{self.server_name}/{target_dir}")
            os.makedirs(host_dir, exist_ok=True)
            filepath = os.path.join(host_dir, filename)
            log_interaction(interaction.user, "플러그인설치", f"{self.server_name} - {selected_label}")
            
            await asyncio.to_thread(urllib.request.urlretrieve, url, filepath)
            
            await interaction.followup.send(f"✅ **자동 설치 완료!**\n`{filename}` 파일이 `{self.server_name}` 서버의 **`{target_dir}`** 폴더에 정확히 꽂혔습니다!\n*(적용하려면 서버를 재시작해주세요.)*", ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 설치 중 오류 발생: {e}", ephemeral=True)

class PluginSelectView(discord.ui.View):
    def __init__(self, server_name: str, server_type: str, hits: list):
        super().__init__(timeout=300) # 5분간 드롭다운 유지
        self.add_item(PluginSelect(server_name, server_type, hits))

class PluginSearchModal(discord.ui.Modal, title='Modrinth 플러그인 / 모드 검색'):
    def __init__(self, server_name: str, server_type: str):
        super().__init__()
        self.server_name = server_name
        self.server_type = server_type

    search_query = discord.ui.TextInput(
        label='검색어 (영어 권장)',
        style=discord.TextStyle.short,
        placeholder='예: essentials, luckperms, lithium',
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ **접근 거부:** 관리자만 가능합니다.", ephemeral=True)
            return

        query = self.search_query.value.strip()
        await interaction.response.send_message(f"🔍 `{query}` 검색 중...", ephemeral=True)
        
        try:
            # API 검색 (비동기 처리)
            hits = await asyncio.to_thread(fetch_modrinth_search, query)
            if not hits:
                await interaction.edit_original_response(content=f"⚠️ `{query}`에 대한 검색 결과가 없습니다.")
                return
            
            # 검색 결과를 드롭다운 View에 담아 전송
            view = PluginSelectView(self.server_name, self.server_type, hits)
            await interaction.edit_original_response(
                content=f"🔍 **Modrinth 검색 결과:** `{query}`\n아래 메뉴를 열어 설치할 항목을 클릭하면 즉시 다운로드됩니다.", 
                view=view
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ 검색 중 오류가 발생했습니다: {e}")

@bot.command(name='상태')
async def check_status(ctx):
    if docker_client:
        try:
            docker_client.ping()
            await ctx.send("🟢 **시스템 정상:** Discord 봇이 실행 중이며 Docker 데몬과 통신할 수 있습니다.")
        except Exception as e:
            await ctx.send(f"⚠️ **Docker 오류:** Docker 데몬에 연결되어 있으나 응답이 없습니다. ({e})")
    else:
        await ctx.send("🔴 **시스템 경고:** Docker 데몬을 찾을 수 없습니다.")

# --- Phase 11: 플러그인/모드 검색 명령어 ---
@bot.command(name='플러그인추가', aliases=['모드추가', '플러그인검색', '모드검색'])
@commands.has_permissions(administrator=True)
async def cmd_search_plugin(ctx, server_name: str, *, query: str):
    """Modrinth에서 플러그인/모드를 검색하고 드롭다운으로 설치합니다."""
    try:
        container = docker_client.containers.get(server_name)
        envs = container.attrs['Config']['Env']
        server_type = "PAPER"
        for e in envs:
            if e.startswith("TYPE="):
                server_type = e.split("=")[1].upper()
                break
                
        msg = await ctx.send(f"🔍 `{server_name}` 서버에 설치할 `{query}` 검색 중...")
        
        hits = await asyncio.to_thread(fetch_modrinth_search, query)
        if not hits:
            await msg.edit(content=f"⚠️ `{query}`에 대한 검색 결과가 없습니다.")
            return
        
        view = PluginSelectView(server_name, server_type, hits)
        await msg.edit(content=f"🔍 **Modrinth 검색 결과:** `{query}`\n아래 메뉴를 열어 설치할 항목을 클릭하면 즉시 다운로드됩니다.", view=view)
        
    except docker.errors.NotFound:
        await ctx.send(f"⚠️ `{server_name}` 서버를 찾을 수 없습니다.")
    except Exception as e:
        await ctx.send(f"❌ 검색 중 오류 발생: {e}")

# --- [수정] 서버생성 명령어 (최신 버전 자동 해석 로직 추가) ---
@bot.command(name='서버생성')
@commands.has_permissions(administrator=True)
async def create_server(ctx, base_name: str, version: str = "LATEST", memory: str = "8G", server_type: str = "PAPER"):
    """새로운 서버를 생성합니다. 예: !서버생성 야생 1.20.4 14G PAPER"""
    s_type = server_type.upper()
    s_version_input = version.upper()
    
    # [신규 핵심 로직] LATEST 입력 시 Mojang API에서 실제 버전 번호 가져오기
    if s_version_input == "LATEST":
        resolved_version = await asyncio.to_thread(get_latest_mc_version)
    else:
        resolved_version = s_version_input.lower()
        
    server_name = f"{base_name}-{resolved_version}-{s_type}".lower().replace(" ", "")
    
    if s_type not in ["PAPER", "ARCLIGHT", "FABRIC"]:
        await ctx.send("⚠️ 서버 타입은 `PAPER`, `ARCLIGHT`, `FABRIC` 중에서만 입력해주세요.")
        return
        
    s_memory_input = memory.strip().lower()
    valid_memories = ["2", "4", "6", "8", "10", "12", "14", "2g", "4g", "6g", "8g", "10g", "12g", "14g"]
    
    if s_memory_input not in valid_memories:
        await ctx.send(f"⚠️ **입력 오류:** 메모리는 지정된 숫자만 선택 가능합니다.")
        return
        
    mem_val = s_memory_input if s_memory_input.endswith('g') else f"{s_memory_input}g"
    jvm_mem_limit = mem_val.upper()

    await ctx.send(f"⏳ 버전 정보를 확인했습니다. `{server_name}` 서버 생성을 준비 중입니다...")
    
    try:
        running_servers = docker_client.containers.list(filters={"ancestor": "itzg/minecraft-server", "status": "running"})
        stopped_servers = []
        for c in running_servers:
            await ctx.send(f"⏳ 다른 서버(`{c.name}`)를 안전하게 종료합니다... (최대 30초 소요)")
            c.stop(timeout=30)
            stopped_servers.append(c.name)

        await ctx.send(f"⏳ `{server_name}` 서버를 생성하고 있습니다. 첫 다운로드 시 시간이 소요될 수 있습니다...")

        host_data_path = os.path.abspath(f"./mc_data/{server_name}")
        os.makedirs(host_data_path, exist_ok=True)

        container = docker_client.containers.run(
            image="itzg/minecraft-server",
            name=server_name,
            detach=True,
            ports={'25565/tcp': 25565}, 
            environment={
                "EULA": "TRUE",
                "VERSION": resolved_version,
                "TYPE": s_type,
                "MEMORY": jvm_mem_limit,

                # --- 최적화 핵심 설정 시작 ---
                "USE_AIKAR_FLAGS": "TRUE",         # GC(가비지 컬렉션) 효율 극대화 (Aikar's Flags)
                "VIEW_DISTANCE": "8",              # 시야 거리 제한 (서버 부하의 주범, 8~10 권장)
                "SIMULATION_DISTANCE": "6",        # 엔티티 연산 거리 (틱 부하 감소)
                "MAX_TICK_TIME": "-1",             # 서버 멈춤 방지 (와치독 비활성화)
                "ENABLE_ROLLING_LOGS": "TRUE",     # 로그 파일 비대화 방지
                "MAX_PLAYERS": "20",               # 최대 인원 제한
                "ONLINE_MODE": "TRUE",             # 정품 인증 (보안)
                # ---------------------------
            },
            volumes={host_data_path: {'bind': '/data', 'mode': 'rw'}},
            mem_limit=mem_val, 
            restart_policy={"Name": "unless-stopped"}
        )
        
        msg = f"✅ **서버 생성 완료!**\n최종 이름: `{server_name}`\n할당 메모리: `{jvm_mem_limit}`"
        if stopped_servers:
            msg += f"\n*(충돌 방지를 위해 자동 종료된 서버: `{', '.join(stopped_servers)}`)*"
            
        await ctx.send(msg)
        
    except docker.errors.APIError as e:
        if "Conflict" in str(e):
            await ctx.send(f"⚠️ 생성 실패: 이미 `{server_name}`(이)라는 컨테이너가 존재합니다.")
        else:
            await ctx.send(f"❌ 오류 발생: {e}")

# --- Phase 2: 서버 종료 명령어 ---
@bot.command(name='서버종료')
@commands.has_permissions(administrator=True)
async def stop_server(ctx, server_name: str):
    """실행 중인 마인크래프트 서버 컨테이너를 안전하게 종료합니다."""
    await ctx.send(f"⏳ `{server_name}` 서버를 안전하게 종료하는 중입니다 (월드 저장 중)...")
    
    try:
        container = docker_client.containers.get(server_name)
        # 안전한 종료(월드 세이브)를 위해 최대 30초 대기 후 SIGKILL
        container.stop(timeout=30) 
        await ctx.send(f"🛑 `{server_name}` 서버가 정상적으로 종료되었습니다.")
        
    except docker.errors.NotFound:
        await ctx.send(f"⚠️ `{server_name}` 서버를 찾을 수 없습니다. 이름이 정확한지 확인해주세요.")
    except Exception as e:
        await ctx.send(f"❌ 서버 종료 중 오류가 발생했습니다: {e}")

# --- Phase 5 수정: 서버 삭제 명령어 (데이터 폴더 동시 삭제) ---
@bot.command(name='서버삭제')
@commands.has_permissions(administrator=True)
async def delete_server(ctx, server_name: str):
    """특정 마인크래프트 서버 컨테이너와 월드 데이터를 완전히 삭제합니다."""
    await ctx.send(f"⏳ `{server_name}` 서버와 연관 데이터를 삭제 준비 중입니다...")
    
    try:
        # 1. 컨테이너 삭제
        try:
            container = docker_client.containers.get(server_name)
            if container.status == 'running':
                await ctx.send(f"⚠️ `{server_name}` 서버가 실행 중입니다. 안전하게 종료 후 삭제를 진행합니다. (최대 30초 소요)")
                container.stop(timeout=30)
            container.remove(force=True)
        except docker.errors.NotFound:
            await ctx.send(f"⚠️ `{server_name}` 컨테이너를 찾을 수 없습니다. (데이터 폴더 삭제를 시도합니다.)")

        # 2. 호스트의 데이터 폴더 영구 삭제
        host_data_path = os.path.abspath(f"./mc_data/{server_name}")
        data_status_msg = ""
        
        if os.path.exists(host_data_path):
            shutil.rmtree(host_data_path)
            data_status_msg = "월드 데이터 영구 삭제 완료"
        else:
            data_status_msg = "삭제할 데이터 폴더가 없음"
            
        await ctx.send(f"🗑️ **완전 삭제 완료:** `{server_name}` 서버 컨테이너가 성공적으로 삭제되었습니다.\n💡 *({data_status_msg})*")
        
    except Exception as e:
        await ctx.send(f"❌ 서버 삭제 중 오류가 발생했습니다: {e}")

# --- 서버 목록 확인 명령어 ---
@bot.command(name='목록', aliases=['서버목록', '리스트'])
async def list_servers(ctx):
    """생성된 모든 마인크래프트 서버의 상태를 확인합니다."""
    if not docker_client:
        await ctx.send("🔴 Docker 데몬과 연결되어 있지 않아 목록을 불러올 수 없습니다.")
        return

    try:
        # itzg/minecraft-server 이미지를 기반으로 만들어진 모든 컨테이너 조회 (정지된 것 포함)
        containers = docker_client.containers.list(all=True, filters={"ancestor": "itzg/minecraft-server"})
        
        if not containers:
            await ctx.send("ℹ️ 현재 생성된 마인크래프트 서버가 없습니다. `!서버생성` 명령어로 새 서버를 만들어보세요!")
            return

        embed = discord.Embed(title="📋 마인크래프트 서버 목록", color=discord.Color.green())
        
        for container in containers:
            # 컨테이너 상태에 따른 이모지 및 텍스트 설정
            if container.status == "running":
                status_emoji = "🟢 실행 중"
            elif container.status == "exited":
                status_emoji = "🔴 정지됨"
            else:
                status_emoji = f"🟡 {container.status}"

            # 포트 매핑 정보 가져오기
            ports = container.attrs['NetworkSettings']['Ports']
            port_info = "포트 정보 없음"
            if ports and '25565/tcp' in ports and ports['25565/tcp']:
                host_port = ports['25565/tcp'][0]['HostPort']
                port_info = f"접속 포트: `{host_port}`"

            embed.add_field(
                name=f"{container.name} {status_emoji}", 
                value=f"ID: `{container.short_id}` | {port_info}", 
                inline=False
            )
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ 서버 목록을 불러오는 중 오류가 발생했습니다: {e}")

# --- Phase 3 & 4 & 5 통합: 개별 서버 제어 버튼 클래스 (삭제 포함) ---
class ServerControlView(discord.ui.View):
    def __init__(self, server_name: str):
        super().__init__(timeout=None)
        self.server_name = server_name
        
        # 1. 서버 시작 버튼
        btn_start = discord.ui.Button(label="서버 시작", style=discord.ButtonStyle.success, emoji="▶️", custom_id=f"start_{server_name}")
        btn_start.callback = self.start_button
        self.add_item(btn_start)
        
        # 2. 서버 종료 버튼
        btn_stop = discord.ui.Button(label="서버 종료", style=discord.ButtonStyle.danger, emoji="⏹️", custom_id=f"stop_{server_name}")
        btn_stop.callback = self.stop_button
        self.add_item(btn_stop)
        
        # 3. 재시작 버튼
        btn_restart = discord.ui.Button(label="재시작", style=discord.ButtonStyle.primary, emoji="🔄", custom_id=f"restart_{server_name}")
        btn_restart.callback = self.restart_button
        self.add_item(btn_restart)

        # 4. [신규] 서버 삭제 버튼 (실수 방지를 위해 회색/Secondary 스타일 적용)
        btn_delete = discord.ui.Button(label="서버 삭제", style=discord.ButtonStyle.secondary, emoji="🗑️", custom_id=f"delete_{server_name}")
        btn_delete.callback = self.delete_button
        self.add_item(btn_delete)

        # [신규] 플러그인 설치 버튼 추가
        btn_plugin = discord.ui.Button(label="플러그인 추가", style=discord.ButtonStyle.secondary, emoji="🧩", custom_id=f"plugin_{server_name}")
        btn_plugin.callback = self.plugin_button
        self.add_item(btn_plugin)

    # --- [신규] View 공통 권한 검사 로직 ---
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 사용자가 관리자 권한이 없는 경우
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ **접근 거부:** 서버 제어 패널은 관리자만 조작할 수 있습니다.", ephemeral=True)
            return False # False를 반환하면 버튼 콜백 함수가 실행되지 않음
        return True

    async def start_button(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            container = docker_client.containers.get(self.server_name)
            if container.status == "running":
                await interaction.followup.send(f"⚠️ `{self.server_name}` 서버가 이미 실행 중입니다.", ephemeral=True)
                return

            # --- [신규] 단일 서버 실행 보장 로직 ---
            running_servers = docker_client.containers.list(filters={"ancestor": "itzg/minecraft-server", "status": "running"})
            stopped_servers = []
            
            for c in running_servers:
                if c.name != self.server_name:
                    await interaction.followup.send(f"⏳ 다른 서버(`{c.name}`)가 사용 중입니다. 포트 충돌 방지를 위해 해당 서버를 안전하게 종료합니다... (최대 30초 소요)", ephemeral=True)
                    c.stop(timeout=30)
                    stopped_servers.append(c.name)
            
            # 다른 서버가 모두 꺼졌으면 현재 서버 시작
            container.start()

            log_interaction(interaction.user, "서버시작", self.server_name)
            
            msg = f"✅ `{self.server_name}` 서버가 시작되었습니다."
            if stopped_servers:
                msg += f"\n*(자동 종료된 서버: `{', '.join(stopped_servers)}`)*"
                
            await interaction.followup.send(msg, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 오류 발생: {e}", ephemeral=True)

    async def stop_button(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            container = docker_client.containers.get(self.server_name)
            if container.status != "running":
                await interaction.followup.send(f"⚠️ `{self.server_name}` 서버가 이미 정지되어 있습니다.", ephemeral=True)
                return
            container.stop(timeout=30)
            log_interaction(interaction.user, "서버정지", self.server_name)

            await interaction.followup.send(f"🛑 `{self.server_name}` 서버가 안전하게 종료되었습니다.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 오류 발생: {e}", ephemeral=True)

    async def restart_button(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            container = docker_client.containers.get(self.server_name)
            if container.status != "running":
                 await interaction.followup.send(f"⚠️ `{self.server_name}` 서버가 실행 중이 아닙니다.", ephemeral=True)
                 return
            container.restart(timeout=30)
            log_interaction(interaction.user, "서버재시작", self.server_name)

            await interaction.followup.send(f"🔄 `{self.server_name}` 서버가 재시작되었습니다.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 오류 발생: {e}", ephemeral=True)

    # --- [수정] 서버 삭제 콜백 함수 (데이터 폴더 동시 삭제) ---
    async def delete_button(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            # 1. 컨테이너 삭제 시도
            try:
                container = docker_client.containers.get(self.server_name)
                if container.status == 'running':
                    await interaction.followup.send(f"⚠️ `{self.server_name}` 서버가 실행 중입니다. 안전하게 종료 후 삭제를 진행합니다. (최대 30초 소요)", ephemeral=True)
                    container.stop(timeout=30)
                container.remove(force=True)
            except docker.errors.NotFound:
                # 컨테이너가 이미 지워졌더라도 폴더 삭제 로직을 타기 위해 예외 처리 패스
                pass 

            log_interaction(interaction.user, "서버삭제", self.server_name)

            # 2. 호스트의 데이터 폴더 영구 삭제
            host_data_path = os.path.abspath(f"./mc_data/{self.server_name}")
            data_status_msg = ""
            
            if os.path.exists(host_data_path):
                shutil.rmtree(host_data_path) # 폴더 내부 파일까지 강제 삭제
                data_status_msg = "월드 데이터 및 설정 파일 영구 삭제 완료"
            else:
                data_status_msg = "삭제할 데이터 폴더가 존재하지 않음"
                
            await interaction.followup.send(f"🗑️ **완전 삭제 완료:** `{self.server_name}` 서버와 연관된 모든 데이터가 삭제되었습니다.\n*(📁 {data_status_msg})*", ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 서버 삭제 중 오류가 발생했습니다: {e}", ephemeral=True)

    # --- ServerControlView 내의 플러그인 버튼 클릭 로직 수정 ---
    async def plugin_button(self, interaction: discord.Interaction):
        try:
            container = docker_client.containers.get(self.server_name)
            envs = container.attrs['Config']['Env']
            server_type = "PAPER"
            for e in envs:
                if e.startswith("TYPE="):
                    server_type = e.split("=")[1].upper()
                    break
            
            # 타입 상관없이 모두 검색 모달 띄우기 (Paper/Arclight는 plugins로, Fabric은 mods로 알아서 들어감)
            await interaction.response.send_modal(PluginSearchModal(self.server_name, server_type))
        except Exception as e:
            await interaction.response.send_message(f"❌ 오류 발생: {e}", ephemeral=True)

class ServerSelect(discord.ui.Select):
    def __init__(self, containers):
        options = []
        for c in containers:
            status_emoji = "🟢" if c.status == "running" else "🔴"
            options.append(discord.SelectOption(label=c.name, description=f"상태: {c.status}", emoji=status_emoji, value=c.name))
        
        if not options:
            options.append(discord.SelectOption(label="생성된 서버 없음", value="none"))
            
        # Select 메뉴에도 고정된 custom_id 부여
        super().__init__(placeholder="🎛️ 제어할 서버를 선택하세요...", min_values=1, max_values=1, options=options, custom_id="dashboard_server_select")

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        if selected_value == "none":
            await interaction.response.send_message("⚠️ 제어할 수 있는 서버가 없습니다.", ephemeral=True)
            return

        view = ServerControlView(selected_value)
        embed = discord.Embed(title=f"🛠️ {selected_value} 개별 제어", description="원하는 작업을 선택하세요. (이 메시지는 본인에게만 보입니다)", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# --- Phase 9: 플러그인 자동 설치 팝업(Modal) ---
class PluginInstallModal(discord.ui.Modal, title='플러그인 자동 설치 (.jar 직접 링크)'):
    def __init__(self, server_name):
        super().__init__()
        self.server_name = server_name

    plugin_url = discord.ui.TextInput(
        label='플러그인 다운로드 URL (직접 다운로드 링크)',
        style=discord.TextStyle.short,
        placeholder='https://example.com/plugin.jar',
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ **접근 거부:** 관리자만 가능합니다.", ephemeral=True)
            return

        url = self.plugin_url.value.strip()
        await interaction.response.send_message(f"⏳ `{self.server_name}` 서버에 플러그인을 다운로드하고 있습니다...", ephemeral=True)

        try:
            # 호스트의 플러그인 폴더 경로 (없으면 자동 생성)
            plugins_dir = os.path.abspath(f"./mc_data/{self.server_name}/plugins")
            os.makedirs(plugins_dir, exist_ok=True)

            # URL에서 파일명 추출 (확장자가 없으면 임의 지정)
            filename = os.path.basename(urllib.parse.urlparse(url).path)
            if not filename.endswith('.jar'):
                filename = "downloaded_plugin.jar"

            filepath = os.path.join(plugins_dir, filename)

            # 봇이 멈추지 않도록 비동기 스레드에서 다운로드 실행
            await asyncio.to_thread(urllib.request.urlretrieve, url, filepath)

            await interaction.edit_original_response(content=f"✅ **설치 완료!**\n`{filename}` 파일이 `{self.server_name}` 서버에 저장되었습니다.\n*(적용하려면 서버를 재시작해주세요.)*")
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ 다운로드 실패: 링크가 유효하지 않거나 오류가 발생했습니다.\n`{e}`")

# --- [수정] 서버 생성용 팝업 (최신 버전 자동 해석 로직 추가) ---
class ServerCreateModal(discord.ui.Modal, title='새 마인크래프트 서버 생성'):
    server_name = discord.ui.TextInput(label='서버 이름 (기본 이름)', style=discord.TextStyle.short, placeholder='예: survival', required=True, max_length=15)
    server_version = discord.ui.TextInput(label='버전 (생략 시 최신 LATEST)', style=discord.TextStyle.short, placeholder='예: 1.20.4', required=False, default='LATEST')
    server_memory = discord.ui.TextInput(label='메모리 (2, 4, 6, 8, 10, 12, 14 중 택1)', style=discord.TextStyle.short, placeholder='예: 8G', required=True, default='8G')
    server_type = discord.ui.TextInput(label='타입 (PAPER, ARCLIGHT, FABRIC)', style=discord.TextStyle.short, placeholder='예: PAPER', required=True, default='PAPER')

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ **접근 거부:** 관리자만 가능합니다.", ephemeral=True)
            return
            
        await interaction.response.send_message("⏳ 서버 설정 및 버전 정보를 확인 중입니다...", ephemeral=True)
            
        base_name = self.server_name.value.strip()
        s_version_input = self.server_version.value.strip().upper() or "LATEST"
        s_memory_input = self.server_memory.value.strip().lower()
        s_type = self.server_type.value.strip().upper()
        
        # [신규 핵심 로직] LATEST 입력 시 Mojang API에서 실제 버전 번호 가져오기
        if s_version_input == "LATEST":
            resolved_version = await asyncio.to_thread(get_latest_mc_version)
        else:
            resolved_version = s_version_input.lower()
            
        # 알아낸 실제 버전으로 도커 컨테이너 이름 짓기
        s_name = f"{base_name}-{resolved_version}-{s_type}".lower().replace(" ", "")
        
        valid_memories = ["2", "4", "6", "8", "10", "12", "14", "2g", "4g", "6g", "8g", "10g", "12g", "14g"]
        if s_memory_input not in valid_memories:
            await interaction.edit_original_response(content=f"⚠️ **입력 오류:** 메모리는 지정된 숫자(단위: G)만 입력 가능합니다.")
            return
        if s_type not in ["PAPER", "ARCLIGHT", "FABRIC"]:
            await interaction.edit_original_response(content=f"⚠️ **입력 오류:** 서버 타입은 `PAPER`, `ARCLIGHT`, `FABRIC` 중에서만 입력해주세요.")
            return
            
        mem_val = s_memory_input if s_memory_input.endswith('g') else f"{s_memory_input}g"
        jvm_mem_limit = mem_val.upper()

        try:
            log_interaction(interaction.user, "서버생성", s_name)
            running_servers = docker_client.containers.list(filters={"ancestor": "itzg/minecraft-server", "status": "running"})
            stopped_servers = []
            for c in running_servers:
                await interaction.edit_original_response(content=f"⏳ 다른 서버(`{c.name}`)를 안전하게 종료합니다... (최대 30초 소요)")
                c.stop(timeout=30)
                stopped_servers.append(c.name)

            await interaction.edit_original_response(content=f"⏳ `{s_name}` 서버를 생성하고 있습니다...")

            host_data_path = os.path.abspath(f"./mc_data/{s_name}")
            os.makedirs(host_data_path, exist_ok=True)

            container = docker_client.containers.run(
                image="itzg/minecraft-server",
                name=s_name,
                detach=True,
                ports={'25565/tcp': 25565}, 
                environment={
                    "EULA": "TRUE",
                    "VERSION": resolved_version, # 도커 내부에도 실제 버전 넘겨주기
                    "TYPE": s_type,
                    "MEMORY": jvm_mem_limit,

                    # --- 최적화 핵심 설정 시작 ---
                    "USE_AIKAR_FLAGS": "TRUE",         # GC(가비지 컬렉션) 효율 극대화 (Aikar's Flags)
                    "VIEW_DISTANCE": "8",              # 시야 거리 제한 (서버 부하의 주범, 8~10 권장)
                    "SIMULATION_DISTANCE": "6",        # 엔티티 연산 거리 (틱 부하 감소)
                    "MAX_TICK_TIME": "-1",             # 서버 멈춤 방지 (와치독 비활성화)
                    "ENABLE_ROLLING_LOGS": "TRUE",     # 로그 파일 비대화 방지
                    "MAX_PLAYERS": "20",               # 최대 인원 제한
                    "ONLINE_MODE": "TRUE",             # 정품 인증 (보안)
                    # ---------------------------
                },
                volumes={host_data_path: {'bind': '/data', 'mode': 'rw'}},
                mem_limit=mem_val, 
                restart_policy={"Name": "unless-stopped"}
            )
            
            msg = f"✅ **서버 생성 완료!**\n최종 이름: `{s_name}`\n할당 메모리: `{jvm_mem_limit}`"
            if stopped_servers:
                msg += f"\n*(충돌 방지를 위해 자동 종료된 서버: `{', '.join(stopped_servers)}`)*"
            await interaction.edit_original_response(content=msg)
            
        except docker.errors.APIError as e:
            if "Conflict" in str(e):
                await interaction.edit_original_response(content=f"⚠️ 생성 실패: 이미 `{s_name}`(이)라는 이름이 존재합니다.")
            else:
                await interaction.edit_original_response(content=f"❌ 오류 발생: {e}")

# --- Phase 4 & 6 통합: 대시보드 UI (드롭다운 + 생성 버튼) ---
class DashboardView(discord.ui.View):
    def __init__(self, containers):
        super().__init__(timeout=None)
        
        # 1. 기존 서버 선택 드롭다운 메뉴 추가
        self.add_item(ServerSelect(containers))
        
        # 2. [신규] 서버 생성 버튼 추가
        btn_create = discord.ui.Button(label="새 서버 생성", style=discord.ButtonStyle.success, emoji="➕", custom_id="dashboard_create_btn")
        btn_create.callback = self.create_server_button
        self.add_item(btn_create)

    # --- [신규] View 공통 권한 검사 로직 ---
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 사용자가 관리자 권한이 없는 경우
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ **접근 거부:** 서버 제어 패널은 관리자만 조작할 수 있습니다.", ephemeral=True)
            return False # False를 반환하면 버튼 콜백 함수가 실행되지 않음
        return True

    # 생성 버튼 클릭 시 팝업(Modal) 띄우기
    async def create_server_button(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ServerCreateModal())

# --- Phase 3: 제어판 패널 호출 명령어 ---
@bot.command(name='제어판', aliases=['제어'])
@commands.has_permissions(administrator=True)
async def server_panel(ctx, server_name: str):
    """특정 마인크래프트 서버를 관리할 수 있는 버튼 UI를 띄웁니다."""
    if not docker_client:
        await ctx.send("🔴 Docker 데몬과 연결되어 있지 않습니다.")
        return

    try:
        # 입력한 이름의 서버(컨테이너)가 실제로 존재하는지 검증
        container = docker_client.containers.get(server_name)
        
        # 상태에 따른 색상 변경
        embed_color = discord.Color.green() if container.status == "running" else discord.Color.red()
        
        embed = discord.Embed(
            title=f"🎛️ {server_name} 제어 패널",
            description="아래 버튼을 클릭하여 서버 상태를 제어하세요.",
            color=embed_color
        )
        embed.add_field(name="현재 상태", value=f"`{container.status.upper()}`", inline=False)
        embed.add_field(name="컨테이너 ID", value=f"`{container.short_id}`", inline=False)
        
        # 앞서 만든 View 클래스를 view 파라미터로 전달
        view = ServerControlView(server_name)
        await ctx.send(embed=embed, view=view)
        
    except docker.errors.NotFound:
        await ctx.send(f"⚠️ `{server_name}` 서버를 찾을 수 없습니다. `!목록` 명령어로 이름을 확인해주세요.")
    except Exception as e:
        await ctx.send(f"❌ 제어판을 불러오는 중 오류가 발생했습니다: {e}")

# 대시보드가 설치된 채널과 메시지 ID를 기억하기 위한 전역 변수
DASHBOARD_CHANNEL_ID = None
DASHBOARD_MESSAGE_ID = None

# --- Phase 4 수정: 실시간 상태 모니터링 (실행 중인 서버만 본문에 표시) ---
@tasks.loop(seconds=15)
async def update_dashboard():
    global DASHBOARD_CHANNEL_ID, DASHBOARD_MESSAGE_ID
    if not DASHBOARD_CHANNEL_ID or not DASHBOARD_MESSAGE_ID:
        return

    channel = bot.get_channel(DASHBOARD_CHANNEL_ID)
    if not channel:
        return

    try:
        message = await channel.fetch_message(DASHBOARD_MESSAGE_ID)
        all_containers = docker_client.containers.list(all=True, filters={"ancestor": "itzg/minecraft-server"})
        
        embed = discord.Embed(title="📊 마인크래프트 통합 모니터링 패널", color=discord.Color.gold())
        
        if not all_containers:
            embed.description = "현재 생성된 서버가 없습니다. 하단의 `➕ 새 서버 생성` 버튼을 이용하세요."
        else:
            embed.description = "서버 상태 및 자원 점유율이 15초 주기로 자동 갱신됩니다."
            running_containers = [c for c in all_containers if c.status == "running"]
            
            if not running_containers:
                embed.add_field(name="현재 상태", value="🔴 **모든 서버가 정지되어 있습니다.**\n아래 드롭다운 메뉴에서 서버를 선택해 시작해보세요.", inline=False)
            else:
                for c in running_containers:
                    # 1. 접속자 수 확인 로직
                    ports = c.attrs['NetworkSettings']['Ports']
                    player_info = "접속자 확인 중..."
                    
                    if ports and '25565/tcp' in ports and ports['25565/tcp']:
                        host_port = ports['25565/tcp'][0]['HostPort']
                        try:
                            server = JavaServer.lookup(f"127.0.0.1:{host_port}")
                            status = server.status()
                            player_info = f"👥 **접속자:** `{status.players.online} / {status.players.max}명`"
                        except Exception:
                            player_info = "👥 **접속자:** `확인 불가 (부팅 중)`"
                    
                    # 2. CPU 및 RAM 자원 사용량 확인 로직
                    stats_text = "💻 자원 로딩 중..."
                    try:
                        # Docker 통계 가져오기 (봇 프리징 방지를 위해 비동기 스레드 사용)
                        stats = await asyncio.to_thread(c.stats, stream=False)
                        
                        # RAM 계산 (사용량 / 제한량)
                        mem_usage = stats['memory_stats'].get('usage', 0)
                        mem_limit = stats['memory_stats'].get('limit', 1)
                        mem_percent = (mem_usage / mem_limit) * 100.0
                        mem_mb = mem_usage / (1024 * 1024)
                        limit_mb = mem_limit / (1024 * 1024)
                        
                        # CPU 계산 (Docker 공식 계산식 적용)
                        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats.get('precpu_stats', {}).get('cpu_usage', {}).get('total_usage', 0)
                        system_cpu_delta = stats['cpu_stats'].get('system_cpu_usage', 0) - stats.get('precpu_stats', {}).get('system_cpu_usage', 0)
                        
                        cpu_percent = 0.0
                        if system_cpu_delta > 0 and cpu_delta > 0:
                            cpus = stats['cpu_stats'].get('online_cpus', 1)
                            cpu_percent = (cpu_delta / system_cpu_delta) * cpus * 100.0
                            
                        stats_text = f"💻 **CPU:** `{cpu_percent:.1f}%` | 🧠 **RAM:** `{mem_mb:.1f}MB / {limit_mb:.1f}MB` (`{mem_percent:.1f}%`)"
                    except Exception as e:
                        stats_text = "💻 자원: `상태를 불러올 수 없습니다.`"
                            
                    # 상태 패널 텍스트 조립
                    status_text = f"🟢 **실행 중 (Online)**\n{player_info}\n{stats_text}"
                    embed.add_field(name=f"서버명: {c.name}", value=status_text, inline=False)
                
        view = DashboardView(all_containers)
        await message.edit(embed=embed, view=view)
        
    except discord.NotFound:
        DASHBOARD_CHANNEL_ID = None
        DASHBOARD_MESSAGE_ID = None
    except Exception as e:
        print(f"대시보드 갱신 오류: {e}")

# --- Phase 4: 대시보드 생성 명령어 ---
@bot.command(name='모니터링시작')
@commands.has_permissions(administrator=True)
async def setup_dashboard(ctx):
    """현재 채널에 실시간 갱신되는 관리 대시보드를 생성합니다."""
    global DASHBOARD_CHANNEL_ID, DASHBOARD_MESSAGE_ID
    
    if not docker_client:
        await ctx.send("🔴 Docker 데몬과 연결되어 있지 않습니다.")
        return

    containers = docker_client.containers.list(all=True, filters={"ancestor": "itzg/minecraft-server"})
    
    embed = discord.Embed(title="📊 마인크래프트 통합 모니터링 패널", description="초기화 중...", color=discord.Color.gold())
    view = DashboardView(containers)
    
    msg = await ctx.send(embed=embed, view=view)
    
    # 메시지 ID 갱신 및 JSON 파일에 저장 (이 부분이 핵심입니다)
    DASHBOARD_CHANNEL_ID = ctx.channel.id
    DASHBOARD_MESSAGE_ID = msg.id
    save_dashboard_data()
    
    await ctx.message.delete()
    
    if not update_dashboard.is_running():
        update_dashboard.start()


# 기존의 기본 help 명령어 비활성화
bot.remove_command('help')

# --- 사용 방법 안내 (도움말) 갱신 ---
@bot.command(name='도움말', aliases=['명령어', 'help'])
async def custom_help(ctx):
    """봇의 사용 방법과 명령어 목록을 안내합니다."""
    embed = discord.Embed(
        title="🛠️ 마인크래프트 서버 관리 봇 도움말", 
        description="Docker 기반 마인크래프트 서버 관리 명령어 목록입니다. (관리자 전용)",
        color=discord.Color.blue()
    )
    embed.add_field(name="`!상태`", value="봇과 Docker 데몬의 연결 상태를 확인합니다.", inline=False)
    embed.add_field(name="`!목록`", value="현재 생성된 모든 마인크래프트 서버 목록과 상태를 조회합니다.", inline=False)
    
    # [수정] 서버생성 명령어 안내 (메모리, 타입 추가)
    embed.add_field(name="`!서버생성 [이름] [버전] [메모리] [타입]`", value="새로운 서버 컨테이너를 생성합니다.\n*타입: PAPER, ARCLIGHT, FABRIC*\n(예: `!서버생성 야생서버 1.20.4 14G PAPER`)", inline=False)
    
    embed.add_field(name="`!서버종료 [서버이름]`", value="실행 중인 서버를 안전하게 저장하고 중지합니다.", inline=False)
    embed.add_field(name="`!서버삭제 [서버이름]`", value="서버 컨테이너와 **월드 데이터를 영구적으로 삭제**합니다.", inline=False)
    
    # [신규] 플러그인 설치 명령어 안내
    embed.add_field(name="`!플러그인설치 [서버이름] [URL]`", value="PAPER 또는 ARCLIGHT 서버에 플러그인(.jar)을 설치합니다.\n(반드시 .jar 직접 다운로드 링크를 입력해야 합니다.)", inline=False)
    
    embed.add_field(name="`!제어판 [서버이름]`", value="특정 서버를 클릭으로 관리할 수 있는 개별 제어 버튼을 띄웁니다.", inline=False)
    embed.add_field(name="`!모니터링시작`", value="⭐ **[추천]** 현재 채널에 실시간으로 상태가 갱신되는 통합 관리 대시보드를 생성합니다.", inline=False)
    
    embed.set_footer(text="버전, 메모리, 타입이 생략될 경우 LATEST, 8G, PAPER가 기본으로 적용됩니다.")
    await ctx.send(embed=embed)

if __name__ == "__main__":
    bot.run(TOKEN)