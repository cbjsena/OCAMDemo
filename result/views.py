# result/views.py
import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from common import messages as msg
from common.menus import RESULT_SIDEBAR_MENU
from result.services import (
    discover_results,
    get_result_detail,
    get_run_data_for_visualizer,
)

logger = logging.getLogger(__name__)

# OCAM 비주얼라이저 index.html 경로
_VISUALIZER_PATH = Path(settings.BASE_DIR) / "ocam" / "visualizer" / "index.html"


@login_required
def result_list(request):
    """결과 목록."""
    results = discover_results()
    return render(
        request,
        "result/result_list.html",
        {
            "current_top_menu": "result",
            "sidebar_menu": RESULT_SIDEBAR_MENU,
            "current_sidebar_menu": "result_list",
            "results": results,
        },
    )


@login_required
def result_view(request, folder):
    """
    OCAM 비주얼라이저(index.html)에 서버 데이터를 주입해서 제공.
    원소스 index.html 을 최소한으로 패치하여 그대로 서빙한다.
    """
    runs = get_run_data_for_visualizer(folder)

    try:
        html = _VISUALIZER_PATH.read_text(encoding="utf-8")
    except OSError:
        messages.error(request, "Visualizer 파일을 찾을 수 없습니다.")
        results = discover_results()
        return render(
            request,
            "result/result_list.html",
            {
                "current_top_menu": "result",
                "sidebar_menu": RESULT_SIDEBAR_MENU,
                "current_sidebar_menu": "result_list",
                "results": results,
            },
        )

    # 1) <head> 에 데이터 주입
    data_script = (
        "<script>\n"
        f"window.__OCAM_RUNS__ = {json.dumps(runs, ensure_ascii=False, default=str)};\n"
        f"window.__OCAM_SELECTED_RUN__ = {json.dumps(folder)};\n"
        "</script>\n"
    )
    html = html.replace("</head>", data_script + "</head>", 1)

    # 2) topbar 에 Back to Results 링크 추가
    back_link = (
        '<a href="/result/" '
        'style="font-size:13px;font-weight:700;color:var(--brand-strong);'
        'text-decoration:none;margin-right:14px;">← Results</a>'
    )
    html = html.replace('<div class="brand">', f'<div class="brand">{back_link}', 1)

    # 3) initializeSource() 를 주입 데이터 우선 사용하도록 교체
    old_init = (
        "    async function initializeSource() {\n"
        "      setEmptyMessage();\n"
        "      setSourceNoticeWaiting();"
    )
    new_init = (
        "    async function initializeSource() {\n"
        "      if (window.__OCAM_RUNS__ && window.__OCAM_RUNS__.length) {\n"
        "        state.runs = window.__OCAM_RUNS__;\n"
        "        state.selectedRun = window.__OCAM_SELECTED_RUN__ || state.runs[0]?.name || '';\n"
        "        if (!state.runs.some(function(r){return r.name===state.selectedRun;})) {\n"
        "          state.selectedRun = state.runs[0]?.name || '';\n"
        "        }\n"
        "        var _sel = state.runs.find(function(r){return r.name===state.selectedRun;});\n"
        "        state.selectedAlgorithm = bestAlgorithmKey(_sel) || '';\n"
        "        state.selectedLowerBound = bestLowerBoundKey(_sel) || '';\n"
        "        els.sourceNotice.textContent = state.runs.length + ' run(s) loaded';\n"
        "        renderAll();\n"
        "        return;\n"
        "      }\n"
        "      setEmptyMessage();\n"
        "      setSourceNoticeWaiting();"
    )
    html = html.replace(old_init, new_init, 1)

    return HttpResponse(html, content_type="text/html; charset=utf-8")


@login_required
def result_detail(request, folder, filename):
    """결과 상세 (legacy JSON 뷰어)."""
    data = get_result_detail(folder, filename)
    if data is None:
        messages.error(request, msg.RESULT_NOT_FOUND)
        results = discover_results()
        return render(
            request,
            "result/result_list.html",
            {
                "current_top_menu": "result",
                "sidebar_menu": RESULT_SIDEBAR_MENU,
                "current_sidebar_menu": "result_list",
                "results": results,
            },
        )

    formatted_json = json.dumps(data, ensure_ascii=False, indent=2)
    results = discover_results()

    return render(
        request,
        "result/result_detail.html",
        {
            "current_top_menu": "result",
            "sidebar_menu": RESULT_SIDEBAR_MENU,
            "current_sidebar_menu": "result_list",
            "results": results,
            "current_folder": folder,
            "current_filename": filename,
            "data": data,
            "formatted_json": formatted_json,
        },
    )


@login_required
def result_leaderboard(request):
    """Leaderboard - 임시 페이지."""
    results = discover_results()
    return render(
        request,
        "result/result_leaderboard.html",
        {
            "current_top_menu": "result",
            "sidebar_menu": RESULT_SIDEBAR_MENU,
            "current_sidebar_menu": "result_leaderboard",
            "results": results,
        },
    )
