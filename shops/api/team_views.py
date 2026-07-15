from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction

from shops.models import ShopMember, Shop
from users.models import User
from shops.api.views import ShopDetailView

def _check_owner(request, shop_id):
    """Check if the requesting user is an OWNER of the shop."""
    return ShopMember.objects.filter(
        user=request.user,
        shop_id=shop_id,
        role="OWNER",
        deleted_at__isnull=True
    ).exists()

class ShopMemberListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        if not shop_id:
            return Response({"detail": "No accessible shop found."}, status=status.HTTP_404_NOT_FOUND)

        members = ShopMember.objects.filter(shop_id=shop_id, deleted_at__isnull=True).select_related("user")
        data = []
        for m in members:
            data.append({
                "id": str(m.id),
                "role": m.role,
                "user": {
                    "id": str(m.user.id),
                    "email": m.user.email,
                    "first_name": m.user.first_name,
                    "last_name": m.user.last_name,
                },
                "created_at": m.created_at
            })
        return Response(data)

class ShopMemberInviteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        if not shop_id:
            return Response({"detail": "No accessible shop found."}, status=status.HTTP_404_NOT_FOUND)

        if not _check_owner(request, shop_id):
            return Response({"detail": "Only shop owners can invite members."}, status=status.HTTP_403_FORBIDDEN)

        email = request.data.get("email")
        role = request.data.get("role", "STAFF")

        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        if role not in dict(ShopMember.ROLE_CHOICES):
            return Response({"detail": "Invalid role."}, status=status.HTTP_400_BAD_REQUEST)

        # In a real system, this would send an invite email.
        # Here we just try to find the user and add them, or fail.
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({"detail": "User not found with this email."}, status=status.HTTP_404_NOT_FOUND)
        
        if ShopMember.objects.filter(shop_id=shop_id, user=user, deleted_at__isnull=True).exists():
            return Response({"detail": "User is already a member."}, status=status.HTTP_400_BAD_REQUEST)
        
        member = ShopMember.objects.create(
            shop_id=shop_id,
            user=user,
            role=role,
            tenant_id=shop_id
        )

        return Response({
            "id": str(member.id),
            "role": member.role,
            "user": {
                "id": str(user.id),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
            }
        }, status=status.HTTP_201_CREATED)

class ShopMemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        if not shop_id:
            return Response({"detail": "No accessible shop found."}, status=status.HTTP_404_NOT_FOUND)

        if not _check_owner(request, shop_id):
            return Response({"detail": "Only shop owners can update member roles."}, status=status.HTTP_403_FORBIDDEN)

        member = get_object_or_404(ShopMember, id=pk, shop_id=shop_id, deleted_at__isnull=True)
        
        # Don't allow changing your own role this way to prevent orphan shops
        if member.user_id == request.user.id:
            return Response({"detail": "Cannot change your own role."}, status=status.HTTP_400_BAD_REQUEST)
        
        role = request.data.get("role")
        if role and role in dict(ShopMember.ROLE_CHOICES):
            member.role = role
            member.save(update_fields=["role", "updated_at"])
            
        return Response({"id": str(member.id), "role": member.role})

    def delete(self, request, pk):
        shop_id = ShopDetailView()._resolve_shop_id(request)
        if not shop_id:
            return Response({"detail": "No accessible shop found."}, status=status.HTTP_404_NOT_FOUND)

        if not _check_owner(request, shop_id):
            return Response({"detail": "Only shop owners can remove members."}, status=status.HTTP_403_FORBIDDEN)

        member = get_object_or_404(ShopMember, id=pk, shop_id=shop_id, deleted_at__isnull=True)
        
        if member.user_id == request.user.id:
            return Response({"detail": "Cannot remove yourself. Delete the shop instead."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Soft delete
        from django.utils import timezone
        member.deleted_at = timezone.now()
        member.save(update_fields=["deleted_at", "updated_at"])
        
        return Response(status=status.HTTP_204_NO_CONTENT)
