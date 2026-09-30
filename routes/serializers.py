from rest_framework import serializers


class LocationField(serializers.Field):
    """Accepts "City, ST", a free-text address, "lat,lng", or {"lat": .., "lng": ..}."""

    default_error_messages = {
        "invalid": 'Use a place like "Chicago, IL", an address, "lat,lng", or {{"lat": .., "lng": ..}}.',
    }

    def to_internal_value(self, data):
        if isinstance(data, dict):
            if "lat" not in data or "lng" not in data:
                self.fail("invalid")
            return {"lat": data["lat"], "lng": data["lng"]}
        if isinstance(data, str) and data.strip():
            return data.strip()
        self.fail("invalid")

    def to_representation(self, value):
        return value


class TripRequestSerializer(serializers.Serializer):
    start = LocationField()
    finish = LocationField()
