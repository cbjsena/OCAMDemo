from django.urls import path

from simulation import views

app_name = "simulation"

urlpatterns = [
    path("", views.simulation_list, name="simulation_list"),
    path("create/", views.simulation_create, name="simulation_create"),
    path("monitoring/", views.simulation_monitoring, name="simulation_monitoring"),
    path(
        "algorithm/upload/",
        views.simulation_algorithm_upload,
        name="simulation_algorithm_upload",
    ),
    path(
        "api/<int:sim_id>/status/",
        views.simulation_status_api,
        name="simulation_status_api",
    ),
    path("api/<int:sim_id>/cancel/", views.simulation_cancel, name="simulation_cancel"),
    path("delete/<int:sim_id>/", views.simulation_delete, name="simulation_delete"),
]
