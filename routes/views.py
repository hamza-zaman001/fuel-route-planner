from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from routes.locations import LocationError
from routes.optimizer import InfeasibleRouteError
from routes.routing import RouteNotFoundError, RoutingError
from routes.serializers import TripRequestSerializer
from routes.services import StationsNotLoadedError, plan_trip


def _plan(data):
    """Validate input and plan the trip. Returns (result, error_response)."""
    serializer = TripRequestSerializer(data=data)
    if not serializer.is_valid():
        return None, (serializer.errors, status.HTTP_400_BAD_REQUEST)
    try:
        return plan_trip(serializer.validated_data["start"], serializer.validated_data["finish"]), None
    except LocationError as exc:
        return None, ({"error": str(exc)}, status.HTTP_400_BAD_REQUEST)
    except (RouteNotFoundError, InfeasibleRouteError) as exc:
        return None, ({"error": str(exc)}, status.HTTP_422_UNPROCESSABLE_ENTITY)
    except RoutingError as exc:
        return None, ({"error": str(exc)}, status.HTTP_502_BAD_GATEWAY)
    except StationsNotLoadedError as exc:
        return None, ({"error": str(exc)}, status.HTTP_503_SERVICE_UNAVAILABLE)


class TripPlanView(APIView):
    """
    POST /api/route/   {"start": "New York, NY", "finish": "Los Angeles, CA"}
    GET  /api/route/?start=New York, NY&finish=Los Angeles, CA
    """

    def post(self, request):
        return self._respond(request, request.data)

    def get(self, request):
        return self._respond(request, request.query_params.dict())

    def _respond(self, request, data):
        result, error = _plan(data)
        if error:
            return Response(*error)
        result["map_url"] = request.build_absolute_uri(result["map_url"])
        return Response(result)


def route_map(request):
    """HTML map of the trip. Reuses the cached route, so it makes no extra API calls."""
    result, error = _plan(request.GET.dict())
    if error:
        body, code = error
        return render(request, "routes/map.html", {"error": body}, status=code)
    return render(request, "routes/map.html", {"trip": result})
