# Podman Container 시작 스크립트 (PowerShell)
# 용도: Django 애플리케이션을 Podman으로 실행

Write-Host "================================"
Write-Host "Docker 컨테이너 시작 스크립트"
Write-Host "================================"
Write-Host ""

# 현재 디렉토리 확인
$PROJECT_DIR = Get-Location
Write-Host "프로젝트 경로: $PROJECT_DIR"
Write-Host ""

# 필요한 폴더 생성
Write-Host "[1/3] 필요한 폴더 생성 중..."
@("outputs", "logs", "instances", "media") | ForEach-Object {
    if (-not (Test-Path $_)) {
        New-Item -ItemType Directory -Path $_ | Out-Null
    }
}
Write-Host "✅ 폴더 생성 완료"
Write-Host ""

# Podman 이미지 빌드 (선택사항)
Write-Host "[2/3] Docker 이미지 빌드 중..."
Write-Host "옵션:"
Write-Host "  [Y] - 이미지 빌드 (코드 변경 시)"
Write-Host "  [N] - 기존 이미지 사용 (빠름)"
$BUILD_IMAGE = Read-Host "선택 (Y/N, 기본값=N)"
$BUILD_IMAGE = if ([string]::IsNullOrEmpty($BUILD_IMAGE)) { "N" } else { $BUILD_IMAGE }

if ($BUILD_IMAGE -eq "Y" -or $BUILD_IMAGE -eq "y") {
    Write-Host "이미지 빌드 시작..."
    podman build -t ocamdemo:latest .
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 이미지 빌드 실패"
        exit 1
    }
    Write-Host "✅ 이미지 빌드 완료"
} else {
    Write-Host "⊙ 기존 이미지 사용"
}
Write-Host ""

# 기존 컨테이너 중지 및 삭제
Write-Host "[3/3] 컨테이너 시작 중..."
Write-Host "기존 컨테이너 정리 중..."
podman stop ocamdemo 2>$null
podman rm ocamdemo 2>$null

# 컨테이너 실행
Write-Host "새 컨테이너 실행 중..."
podman run -d `
  --name ocamdemo `
  -p 8000:8000 `
  -v "$PROJECT_DIR\outputs:/app/outputs" `
  -v "$PROJECT_DIR\logs:/app/logs" `
  -v "$PROJECT_DIR\instances:/app/instances" `
  -v "$PROJECT_DIR\media:/app/media" `
  -v "$PROJECT_DIR\db.sqlite3:/app/db.sqlite3" `
  --env-file .env.docker `
  ocamdemo:latest

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 컨테이너 시작 실패"
    exit 1
}

Write-Host "✅ 컨테이너 시작 성공"
Write-Host ""

# 컨테이너 상태 확인
Write-Host "컨테이너 상태:"
podman ps -f name=ocamdemo
Write-Host ""

# 로그 출력
Write-Host "================================"
Write-Host "Django 애플리케이션 로그"
Write-Host "================================"
Write-Host "(Ctrl+C로 중단)"
Write-Host ""

# 로그 실시간 출력
podman logs -f ocamdemo 2>$null

# 종료 시 정리
Write-Host ""
Write-Host "컨테이너를 중지합니다..."
podman stop ocamdemo 2>$null
Write-Host "✅ 완료"

