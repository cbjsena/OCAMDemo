from django.http import JsonResponse


def api_root(request):
    return JsonResponse(
        {
            "status": "ok",
            "project": "OCAMDemo",
            "endpoints": {
                "instances": "/api/instances/",
                "algorithms": "/api/algorithms/",
                "simulations": "/api/simulations/",
            },
        }
    )


def api_instances(request):
    from instance.services.instance_service import discover_instances

    instances = discover_instances()
    return JsonResponse(
        {
            "count": len(instances),
            "instances": [{"name": i["name"], "files": i["files"]} for i in instances],
        }
    )


def api_algorithms(request):
    from simulation.algorithm_scanner import discover_algorithms

    algorithms = discover_algorithms()
    return JsonResponse(
        {
            "count": len(algorithms),
            "algorithms": [{"full_name": a["full_name"], "valid": a["valid"]} for a in algorithms],
        }
    )
