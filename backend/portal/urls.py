"""
URL configuration for portal project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from identity import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/companies/', views.CompaniesView.as_view()),
    path('api/auth/marketing/start/', views.MarketingLoginStartView.as_view()),
    path('api/auth/marketing/login/', views.MarketingCredentialsLoginView.as_view()),
    path('api/auth/marketing/callback/', views.MarketingLoginCallbackView.as_view()),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='portal-token-refresh'),
    path('api/portal/workspace/', views.WorkspaceView.as_view()),
    path('api/portal/sso/preflight/', views.SSOPreflightView.as_view()),
    path('api/portal/sso/launch/', views.SSOLaunchView.as_view()),
    path('api/portal/sso/exchange/', views.SSOExchangeView.as_view()),
]
