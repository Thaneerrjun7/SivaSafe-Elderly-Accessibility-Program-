import asyncio
from winrt.windows.devices.geolocation import Geolocator, GeolocationAccessStatus

async def get_location():
    try:
        status = await Geolocator.request_access_async()
        if status == GeolocationAccessStatus.ALLOWED:
            geolocator = Geolocator()
            pos = await geolocator.get_geoposition_async()
            coord = pos.coordinate
            print(f"{coord.point.position.latitude},{coord.point.position.longitude},{coord.accuracy}")
        else:
            print(f"Access denied: {status.name}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(get_location())
