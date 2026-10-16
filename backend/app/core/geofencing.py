import ipaddress
import logging

from geopy.distance import geodesic
from app.config import settings

logger = logging.getLogger(__name__)


def is_within_office_boundary(latitude: float, longitude: float) -> bool:
    """
    Check if the given coordinates are within the office boundary
    using geofencing.
    
    Args:
        latitude: User's latitude
        longitude: User's longitude
        
    Returns:
        bool: True if within office boundary, False otherwise
    """
    office_location = (settings.OFFICE_LATITUDE, settings.OFFICE_LONGITUDE)
    user_location = (latitude, longitude)
    
    # Calculate distance in meters
    distance = geodesic(office_location, user_location).meters
    
    return distance <= settings.OFFICE_RADIUS_METERS


def get_distance_from_office(latitude: float, longitude: float) -> float:
    """
    Get the distance in meters from the office location
    
    Args:
        latitude: User's latitude
        longitude: User's longitude
        
    Returns:
        float: Distance in meters
    """
    office_location = (settings.OFFICE_LATITUDE, settings.OFFICE_LONGITUDE)
    user_location = (latitude, longitude)
    
    return geodesic(office_location, user_location).meters



def office_networks() -> list:
    """Parsed OFFICE_IP_RANGES. Bad entries are logged and skipped, never fatal."""
    nets = []
    for raw in (settings.OFFICE_IP_RANGES or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid entry in OFFICE_IP_RANGES: %r", raw)
    return nets


def is_office_network(ip: str) -> bool:
    """
    Whether a request came from the office connection.

    GPS coordinates are supplied by the client and can be set to anything
    (DevTools > Sensors, or a plain curl), so the geofence alone proves
    nothing about where someone is. The source address is chosen by the
    network, not the caller, which is why this is the check that actually
    binds a check-in to the office.

    NOTE: this must list the office's *public* address. A private range such
    as 192.168.x.x is never visible to a server outside the LAN — every
    request from the office arrives NAT-translated to one public IP.
    """
    nets = office_networks()
    if not nets or not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in nets)
