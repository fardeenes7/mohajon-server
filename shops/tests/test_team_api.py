import pytest
from rest_framework.test import APIClient
from rest_framework import status
from django.urls import reverse
from shops.models import Shop, ShopMember
from users.models import User

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def owner_user():
    return User.objects.create_user(email="owner@test.com", password="password")

@pytest.fixture
def other_owner_user():
    return User.objects.create_user(email="otherowner@test.com", password="password")

@pytest.fixture
def staff_user():
    return User.objects.create_user(email="staff@test.com", password="password")

@pytest.fixture
def shop(owner_user, staff_user):
    shop = Shop.objects.create(name="Test Shop", subdomain="testshop", created_by=owner_user)
    ShopMember.objects.create(shop=shop, user=owner_user, role="OWNER", tenant_id=shop.id)
    ShopMember.objects.create(shop=shop, user=staff_user, role="STAFF", tenant_id=shop.id)
    return shop

@pytest.fixture
def other_shop(other_owner_user):
    shop = Shop.objects.create(name="Other Shop", subdomain="othershop", created_by=other_owner_user)
    ShopMember.objects.create(shop=shop, user=other_owner_user, role="OWNER", tenant_id=shop.id)
    return shop

@pytest.mark.django_db
def test_shop_team_list(api_client, shop, owner_user, other_shop):
    api_client.force_authenticate(user=owner_user)
    url = reverse('shop_team_list')
    
    # Needs tenant_id via headers for TenantMiddleware, or active context resolution handles it
    response = api_client.get(url, HTTP_X_TENANT_ID=str(shop.id))
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2

@pytest.mark.django_db
def test_shop_team_invite_as_owner(api_client, shop, owner_user):
    User.objects.create_user(email="newuser@test.com", password="password")
    
    api_client.force_authenticate(user=owner_user)
    url = reverse('shop_team_invite')
    
    response = api_client.post(url, {"email": "newuser@test.com", "role": "STAFF"}, HTTP_X_TENANT_ID=str(shop.id))
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["user"]["email"] == "newuser@test.com"

@pytest.mark.django_db
def test_shop_team_invite_as_staff(api_client, shop, staff_user):
    User.objects.create_user(email="newuser@test.com", password="password")
    
    api_client.force_authenticate(user=staff_user)
    url = reverse('shop_team_invite')
    
    response = api_client.post(url, {"email": "newuser@test.com", "role": "STAFF"}, HTTP_X_TENANT_ID=str(shop.id))
    assert response.status_code == status.HTTP_403_FORBIDDEN

@pytest.mark.django_db
def test_shop_team_tenant_isolation(api_client, shop, other_shop, owner_user, other_owner_user):
    # Owner of shop tries to list members of other_shop using other_shop's tenant ID
    api_client.force_authenticate(user=owner_user)
    url = reverse('shop_team_list')
    
    response = api_client.get(url, HTTP_X_TENANT_ID=str(other_shop.id))
    assert response.status_code == status.HTTP_404_NOT_FOUND
