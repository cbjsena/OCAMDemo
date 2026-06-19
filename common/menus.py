# common/menus.py
"""
메뉴 구조 정의 (항목 C)

3개 대메뉴: Instance, Simulation, Result
- 우측 상단에 고정 표시
- 클릭 시 좌측 사이드바에 하위 메뉴 표시
"""


class TopMenu:
    INSTANCE = "instance"
    SIMULATION = "simulation"
    RESULT = "result"


# 상단 고정 메뉴 (우측 상단)
TOP_MENU_ITEMS = [
    {
        "name": "Instance",
        "key": TopMenu.INSTANCE,
        "url_name": "instance:instance_list",
        "icon": "bi-database",
    },
    {
        "name": "Simulation",
        "key": TopMenu.SIMULATION,
        "url_name": "simulation:simulation_list",
        "icon": "bi-play-circle",
    },
    {
        "name": "Result",
        "key": TopMenu.RESULT,
        "url_name": "result:result_list",
        "icon": "bi-bar-chart-line",
    },
]


# Instance 하위 메뉴 (좌측 사이드바)
INSTANCE_SIDEBAR_MENU = [
    {
        "name": "Instance List",
        "url_name": "instance:instance_list",
        "icon": "bi-list-ul",
    },
    {
        "name": "Instance Upload",
        "url_name": "instance:instance_upload",
        "icon": "bi-upload",
    },
    {
        "name": "Compare Instances",
        "url_name": "instance:instance_compare",
        "icon": "bi-diagram-2",
    },
]


# Simulation 하위 메뉴 (좌측 사이드바)
SIMULATION_SIDEBAR_MENU = [
    {
        "key": "simulation_list",
        "name": "Simulation List",
        "url_name": "simulation:simulation_list",
        "icon": "bi-list-ul",
    },
    {
        "key": "simulation_create",
        "name": "Create Simulation",
        "url_name": "simulation:simulation_create",
        "icon": "bi-plus-circle",
    },
    {
        "key": "simulation_monitoring",
        "name": "Monitoring",
        "url_name": "simulation:simulation_monitoring",
        "icon": "bi-broadcast-pin",
    },
    {
        "key": "simulation_algorithm_upload",
        "name": "Algorithm Upload",
        "url_name": "simulation:simulation_algorithm_upload",
        "icon": "bi-cloud-upload",
    },
]


# Result 하위 메뉴 (좌측 사이드바)
RESULT_SIDEBAR_MENU = [
    {
        "key": "result_list",
        "name": "Result List",
        "url_name": "result:result_list",
        "icon": "bi-list-ul",
    },
    {
        "key": "result_leaderboard",
        "name": "Leaderboard",
        "url_name": "result:result_leaderboard",
        "icon": "bi-trophy",
    },
]
