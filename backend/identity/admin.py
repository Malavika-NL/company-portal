from django.contrib import admin
from .models import ApplicationUserMapping, Company, MarketingAuthorizationState, Membership, PortalProfile, SSOCode

admin.site.register(Company)
admin.site.register(Membership)
admin.site.register(PortalProfile)
admin.site.register(ApplicationUserMapping)
admin.site.register(SSOCode)
admin.site.register(MarketingAuthorizationState)

# Register your models here.
