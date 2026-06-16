# common/context_processors.py
"""항목 C: 모든 템플릿에 메뉴 구조를 자동 전달"""

from common.menus import TOP_MENU_ITEMS


def global_menus(request):
    return {
        "top_menu_items": TOP_MENU_ITEMS,
    }
