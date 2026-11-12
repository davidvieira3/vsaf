from django.urls import path

from . import views

app_name = "inventory"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("vue/<slug:key>/", views.grid_page, name="grid"),
    path("api/vue/<slug:key>/", views.grid_data, name="grid_data"),
    path("api/vue/<slug:key>/<int:pk>/", views.grid_detail, name="grid_detail"),
    path("export/vue/<slug:key>.<str:fmt>", views.grid_export, name="grid_export"),
    path("export/inventaire.xlsx", views.workbook_export, name="workbook"),
]
