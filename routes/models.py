from django.db import models


class Station(models.Model):
    """A truck stop from the provided price list, geocoded to its city."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=6, decimal_places=3, help_text="USD per gallon")
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        ordering = ["opis_id"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"


class City(models.Model):
    """US city centroid, used to geocode stations and "City, ST" inputs offline."""

    name = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    lookup_key = models.CharField(max_length=120, unique=True, help_text="Normalized 'ST|CITY NAME'")
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        verbose_name_plural = "cities"
        ordering = ["state", "name"]

    def __str__(self):
        return f"{self.name}, {self.state}"
