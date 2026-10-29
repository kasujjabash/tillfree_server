"""Create a demo pharmacy to click around in:  python manage.py seed_demo"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, Membership
from apps.catalog.models import Category, Product
from apps.inventory.services import receive_stock

PRODUCTS = [
    # name, generic, category, unit, price, cost, qty, days_to_expiry, rx
    ("Panadol 500mg", "Paracetamol", "Pain relief", "strip", 2000, 1200, 120, 400, False),
    ("Hedex", "Paracetamol + Caffeine", "Pain relief", "strip", 1500, 900, 80, 300, False),
    ("Amoxyl 500mg", "Amoxicillin", "Antibiotics", "capsule pack", 6000, 3500, 40, 200, True),
    ("Coartem 80/480", "Artemether/Lumefantrine", "Antimalarials", "pack", 12000, 7500, 30, 60, True),
    ("ORS sachet", "Oral rehydration salts", "Rehydration", "sachet", 500, 250, 200, 700, False),
    ("Vitamin C 1000mg", "Ascorbic acid", "Vitamins", "tube", 15000, 9000, 4, 500, False),
]


class Command(BaseCommand):
    help = "Create a demo pharmacy with staff, products and stock."

    @transaction.atomic
    def handle(self, *args, **options):
        business, _ = Business.objects.get_or_create(
            slug="demo-pharmacy",
            defaults={"name": "Demo Pharmacy", "industry_pack": "pharmacy", "phone": "+256700000000",
                      "address": "Kampala Road, Kampala"},
        )
        branch, _ = Branch.objects.get_or_create(business=business, name="Main branch", defaults={"is_main": True})

        for username, role in [("owner", "owner"), ("pharmacist", "pharmacist"), ("cashier", "cashier")]:
            user, created = User.objects.get_or_create(username=username, defaults={"first_name": username.title()})
            if created:
                user.set_password("demo12345")
                user.save()
            Membership.objects.get_or_create(user=user, business=business, defaults={"role": role})

        if Product.objects.filter(business=business).exists():
            self.stdout.write("Demo products already exist; skipped stock.")
        else:
            owner = User.objects.get(username="owner")
            today = timezone.localdate()
            for name, generic, cat, unit, price, cost, qty, days, rx in PRODUCTS:
                category, _ = Category.objects.get_or_create(business=business, name=cat, parent=None)
                product = Product.objects.create(
                    business=business, name=name, generic_name=generic, category=category, unit=unit,
                    selling_price=Decimal(price), requires_prescription=rx, reorder_level=10,
                )
                receive_stock(business=business, branch=branch, product=product, quantity=qty,
                              cost_price=Decimal(cost), user=owner, batch_number=f"B{product.pk:04d}",
                              expiry_date=today + timedelta(days=days))

        self.stdout.write(self.style.SUCCESS(
            "Demo pharmacy ready. Logins (password demo12345): owner, pharmacist, cashier. "
            "Public store: /api/v1/store/demo-pharmacy/products/"
        ))
