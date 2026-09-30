from django.urls import path

from routes import views

urlpatterns = [
    path("route/", views.TripPlanView.as_view(), name="route-plan"),
    path("route/map/", views.route_map, name="route-map"),
]
