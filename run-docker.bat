@echo off
REM Podman Container 시작 스크립트 (Windows)
REM 용도: Django 애플리케이션을 Podman으로 실행

REM 한글 깨짐 방지 - UTF-8로 변경
chcp 65001 >nul

setlocal enabledelayedexpansion

echo ================================
echo Docker 컨테이너 시작 스크립트
echo ================================
echo.

REM 현재 디렉토리 확인
for /f "delims=" %%A in ('cd') do set "PROJECT_DIR=%%A"
echo 프로젝트 경로: %PROJECT_DIR%
echo.

REM 필요한 폴더 생성
echo [1/3] 필요한 폴더 생성 중...
if not exist outputs mkdir outputs
if not exist logs mkdir logs
if not exist instances mkdir instances
if not exist media mkdir media
echo ✅ 폴더 생성 완료
echo.

REM Podman 이미지 빌드 (선택사항)
echo [2/3] Docker 이미지 빌드 중...
echo 옵션:
echo   [Y] - 이미지 빌드 (코드 변경 시)
echo   [N] - 기존 이미지 사용 (빠름)
set /p BUILD_IMAGE="선택 (Y/N, 기본값=N): "
if /i "!BUILD_IMAGE!"=="Y" (
    echo 이미지 빌드 시작...
    podman build -t ocamdemo:latest .
    if !errorlevel! neq 0 (
        echo ❌ 이미지 빌드 실패
        exit /b 1
    )
    echo ✅ 이미지 빌드 완료
) else (
    echo ⊙ 기존 이미지 사용
)
echo.

REM 기존 컨테이너 중지 및 삭제
echo [3/3] 컨테이너 시작 중...
echo 기존 컨테이너 정리 중...
podman stop ocamdemo >nul 2>&1
podman rm ocamdemo >nul 2>&1

REM 컨테이너 실행
echo 새 컨테이너 실행 중...
podman run -d ^
  --name ocamdemo ^
  -p 8000:8000 ^
  -v "%PROJECT_DIR%\outputs:/app/outputs" ^
  -v "%PROJECT_DIR%\logs:/app/logs" ^
  -v "%PROJECT_DIR%\instances:/app/instances" ^
  -v "%PROJECT_DIR%\media:/app/media" ^
  -v "%PROJECT_DIR%\db.sqlite3:/app/db.sqlite3" ^
  --env-file .env.docker ^
  ocamdemo:latest

if !errorlevel! neq 0 (
    echo ❌ 컨테이너 시작 실패
    exit /b 1
)

echo ✅ 컨테이너 시작 성공
echo.

REM 컨테이너 상태 확인
echo 컨테이너 상태:
podman ps -f name=ocamdemo
echo.

REM 로그 출력
echo ================================
echo Django 애플리케이션 로그
echo ================================
echo (Ctrl+C로 중단)
echo.
podman logs -f ocamdemo

REM 종료 시 정리
echo.
echo 컨테이너를 중지합니다...
podman stop ocamdemo
echo ✅ 완료

endlocal

