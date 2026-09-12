__all__ = ()

from django.views.generic import TemplateView


class Home(TemplateView):
    template_name = "homepage/home.html"
