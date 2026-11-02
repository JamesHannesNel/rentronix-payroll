"""Which companies a signed-in user may see, and which one they're looking at."""

from __future__ import annotations

from apps.core.models import Company, Membership

SESSION_KEY = "company_id"
PAY_ROLES = {Membership.Role.OWNER, Membership.Role.PAYROLL_ADMIN}


def companies_for(user):
    qs = Company.objects.filter(is_active=True)
    if user.is_superuser:
        return qs
    return qs.filter(memberships__user=user)


def current_company(request) -> Company | None:
    companies = companies_for(request.user)
    chosen = request.session.get(SESSION_KEY)
    company = companies.filter(pk=chosen).first() if chosen else None
    if company is None:
        company = companies.order_by("name").first()
        if company is not None:
            request.session[SESSION_KEY] = company.pk
    return company


def can_pay(user, company) -> bool:
    """May this user prepare and approve payments for this company?"""
    if user.is_superuser:
        return True
    return Membership.objects.filter(user=user, company=company, role__in=PAY_ROLES).exists()


def nav(request):
    """Template context processor for the app's chrome."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    company = getattr(request, "company", None)
    return {
        "nav_companies": companies_for(request.user).order_by("name"),
        "nav_company": company,
        "nav_can_pay": bool(company and can_pay(request.user, company)),
        "nav_is_owner": bool(company and (request.user.is_superuser or Membership.objects.filter(
            user=request.user, company=company, role=Membership.Role.OWNER).exists())),
    }
