from django.urls import path

from result import views

app_name = "result"

urlpatterns = [
    path("", views.result_list, name="result_list"),
    path("leaderboard/", views.result_leaderboard, name="result_leaderboard"),
    path("<str:folder>/view/", views.result_view, name="result_view"),
    path("<str:folder>/<str:filename>/", views.result_detail, name="result_detail"),
]
