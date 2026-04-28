import socket
import logging

logger = logging.getLogger(__name__)

# The canonical IPs that shops should point to
EXPECTED_A_RECORDS = ["104.21.10.10", "104.21.10.11"]

def verify_dns_readiness(domain: str) -> dict:
    """
    Verifies if a domain is properly pointing to the Mohajon infrastructure.
    Returns a dict with 'valid' and 'reason'.
    """
    try:
        # Simplistic resolution for health check.
        # In a real environment, we would use dnspython to check specifically for CNAME or A records.
        resolved_ip = socket.gethostbyname(domain)
        
        # Check if the resolved IP is one of our expected load balancer/Traefik IPs
        if resolved_ip in EXPECTED_A_RECORDS:
            return {"valid": True, "reason": "DNS is correctly pointing to our infrastructure."}
        
        # Check if it's pointing to Cloudflare Proxy IPs (very basic mock check)
        if resolved_ip.startswith("104.") or resolved_ip.startswith("172."):
            # Cloudflare proxy IPs
            return {
                "valid": False, 
                "reason": "Domain seems to be proxied via Cloudflare. Please disable proxy (Orange Cloud) during SSL provisioning."
            }
            
        return {"valid": False, "reason": f"Domain points to unknown IP ({resolved_ip})."}
        
    except socket.gaierror:
        return {"valid": False, "reason": "DNS resolution failed. Domain might not be registered or propagated."}
    except Exception as e:
        logger.error(f"DNS check failed for {domain}: {str(e)}")
        return {"valid": False, "reason": "Internal validation error."}

