from django.urls import path

from api import views

app_name = "api"

urlpatterns = [
    path("", views.api_root, name="api_root"),
    path("instances/", views.api_instances, name="api_instances"),
    path("algorithms/", views.api_algorithms, name="api_algorithms"),
]
