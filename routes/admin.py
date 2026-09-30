from django.contrib import admin

from routes.models import City, Station


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "price")
    list_filter = ("state",)
    search_fields = ("name", "city", "opis_id")


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "state", "latitude", "longitude")
    list_filter = ("state",)
    search_fields = ("name",)
