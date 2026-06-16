from django.urls import path

from instance import views

app_name = "instance"

urlpatterns = [
    path("", views.instance_list, name="instance_list"),
    path(
        "upload/",
        views.instance_upload,
        name="instance_upload",
    ),
    path(
        "compare/",
        views.instance_compare,
        name="instance_compare",
    ),
    path(
        "<str:instance_name>/download/",
        views.instance_folder_download,
        name="instance_folder_download",
    ),
    path(
        "<str:instance_name>/",
        views.instance_detail,
        name="instance_detail_default",
    ),
    path(
        "<str:instance_name>/<str:filename>/",
        views.instance_detail,
        name="instance_detail",
    ),
    path(
        "<str:instance_name>/<str:filename>/download/",
        views.csv_download,
        name="csv_download",
    ),
    path(
        "<str:instance_name>/<str:filename>/upload/",
        views.csv_upload,
        name="csv_upload",
    ),
]
