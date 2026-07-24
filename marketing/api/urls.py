from django.urls import path
from .views import WaitlistCreateView, AdminWaitlistView, AdminWaitlistApproveView
from .social_views import (
    SocialOAuthStartView,
    SocialOAuthCallbackView,
    SocialConnectionListCreateView,
    SocialConnectionDisconnectView,
    SocialPublishView,
    SocialBulkPublishView,
    ProductSocialActivityView,
)

urlpatterns = [
    # Public
    path('waitlist/', WaitlistCreateView.as_view(), name='waitlist_create'),
    
    # Operations
    path('admin/waitlist/', AdminWaitlistView.as_view(), name='admin_waitlist_list'),
    path('admin/waitlist/<int:pk>/approve/', AdminWaitlistApproveView.as_view(), name='admin_waitlist_approve'),

    # Social connection + publishing (v0.4)
    path('social/connect/start/', SocialOAuthStartView.as_view(), name='social_oauth_start'),
    path('social/connect/callback/', SocialOAuthCallbackView.as_view(), name='social_oauth_callback'),
    path('social/connections/', SocialConnectionListCreateView.as_view(), name='social_connection_list_create'),
    path('social/connections/<uuid:connection_id>/disconnect/', SocialConnectionDisconnectView.as_view(), name='social_connection_disconnect'),
    path('social/publish/', SocialPublishView.as_view(), name='social_publish_single'),
    path('social/publish/bulk/', SocialBulkPublishView.as_view(), name='social_publish_bulk'),
    path('social/products/<uuid:product_id>/activity/', ProductSocialActivityView.as_view(), name='social_product_activity'),
]
