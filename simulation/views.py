# simulation/views.py
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from common import messages as msg
from common.constants import SIMULATION_STATUS_CANCELED
from common.menus import SIMULATION_SIDEBAR_MENU
from instance.services.instance_service import discover_instances
from simulation.algorithm_scanner import discover_algorithms, install_algorithm_zip
from simulation.models import SimulationRun
from simulation.tasks import run_simulation_task

logger = logging.getLogger(__name__)


@login_required
def simulation_list(request):
    """시뮬레이션 전체 목록."""
    simulations = SimulationRun.objects.all()
    return render(
        request,
        "simulation/simulation_list.html",
        {
            "current_top_menu": "simulation",
            "sidebar_menu": SIMULATION_SIDEBAR_MENU,
            "current_sidebar_menu": "simulation_list",
            "simulations": simulations,
        },
    )


@login_required
def simulation_create(request):
    """시뮬레이션 생성 — Instance + Algorithm 선택."""
    instances = discover_instances()
    algorithms = discover_algorithms()

    selected_instance_name = request.GET.get("instance_name", "")
    valid_instance_names = {inst["name"] for inst in instances}
    if selected_instance_name not in valid_instance_names:
        selected_instance_name = ""

    if request.method == "POST":
        instance_name = request.POST.get("instance_name")
        algorithm_name = request.POST.get("algorithm_name")

        if not instance_name or not algorithm_name:
            messages.error(request, msg.INVALID_PARAMETERS)
            return redirect("simulation:simulation_create")

        sim = SimulationRun.objects.create(
            instance_name=instance_name,
            algorithm_name=algorithm_name,
            description=request.POST.get("description", ""),
            created_by=request.user,
        )

        # Celery 태스크 실행
        task = run_simulation_task.delay(sim.id)
        sim.task_id = task.id
        sim.save(update_fields=["task_id"])

        messages.success(request, msg.SIMULATION_STARTED.format(sim_id=sim.id))
        return redirect("simulation:simulation_monitoring")

    return render(
        request,
        "simulation/simulation_create.html",
        {
            "current_top_menu": "simulation",
            "sidebar_menu": SIMULATION_SIDEBAR_MENU,
            "current_sidebar_menu": "simulation_create",
            "instances": instances,
            "algorithms": algorithms,
            "selected_instance_name": selected_instance_name,
        },
    )


@login_required
def simulation_monitoring(request):
    """진행 중인 시뮬레이션 모니터링 (항목 E)."""
    running = SimulationRun.objects.filter(status__in=["PENDING", "RUNNING"])
    return render(
        request,
        "simulation/simulation_monitoring.html",
        {
            "current_top_menu": "simulation",
            "sidebar_menu": SIMULATION_SIDEBAR_MENU,
            "current_sidebar_menu": "simulation_monitoring",
            "simulations": running,
        },
    )


@login_required
def simulation_algorithm_upload(request):
    """알고리즘 ZIP 업로드 화면/처리."""
    if request.method == "POST":
        upload_file = request.FILES.get("algorithm_zip")
        if not upload_file:
            messages.error(request, msg.FILE_NOT_SELECTED)
            return redirect("simulation:simulation_algorithm_upload")

        if not upload_file.name.lower().endswith(".zip"):
            messages.error(request, msg.INVALID_FILE_EXT.format(ext="zip"))
            return redirect("simulation:simulation_algorithm_upload")

        try:
            installed = install_algorithm_zip(upload_file)
            messages.success(
                request,
                msg.ALGORITHM_UPLOAD_SUCCESS.format(name=installed["full_name"]),
            )
            return redirect("simulation:simulation_create")
        except FileExistsError as e:
            # 예: Algorithm 'yongs/only_virtual' already exists
            name = str(e).split("'")[1] if "'" in str(e) else str(e)
            messages.error(request, msg.ALGORITHM_UPLOAD_EXISTS.format(name=name))
        except ValueError as e:
            messages.error(request, msg.ALGORITHM_UPLOAD_INVALID.format(reason=str(e)))
        except Exception as e:
            logger.exception("Algorithm upload failed")
            messages.error(request, msg.SAVE_ERROR.format(target="algorithm", error=str(e)))

        return redirect("simulation:simulation_algorithm_upload")

    return render(
        request,
        "simulation/algorithm_upload.html",
        {
            "current_top_menu": "simulation",
            "sidebar_menu": SIMULATION_SIDEBAR_MENU,
            "current_sidebar_menu": "simulation_algorithm_upload",
        },
    )


@login_required
def simulation_status_api(request, sim_id):
    """AJAX polling용 API — 진행률 반환 (항목 E)."""
    sim = get_object_or_404(SimulationRun, pk=sim_id)
    return JsonResponse(
        {
            "id": sim.id,
            "status": sim.status,
            "progress": sim.progress,
            "model_status": sim.model_status or "",
        }
    )


@login_required
def simulation_cancel(request, sim_id):
    """시뮬레이션 중단 (항목 E)."""
    sim = get_object_or_404(SimulationRun, pk=sim_id)
    if sim.can_cancel:
        sim.status = SIMULATION_STATUS_CANCELED
        sim.model_status = "Canceled by user"
        sim.save(update_fields=["status", "model_status", "updated_at"])
        messages.success(request, msg.SIMULATION_CANCELED.format(sim_id=sim.id))
    else:
        messages.warning(request, msg.SIMULATION_CANCEL_FAILED.format(sim_id=sim.id))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"status": sim.status})
    return redirect("simulation:simulation_monitoring")
