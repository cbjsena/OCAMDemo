from django.contrib import admin

from simulation.models import SimulationRun


@admin.register(SimulationRun)
class SimulationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "instance_name",
        "algorithm_name",
        "status",
        "progress",
        "created_at",
    )
    list_filter = ("status",)
    readonly_fields = ("created_at", "updated_at")
