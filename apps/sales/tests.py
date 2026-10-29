from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, Membership
from apps.catalog.models import Product
from apps.inventory.models import StockMovement
from apps.inventory.services import receive_stock


class PharmacyFlowTests(APITestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.biz = Business.objects.create(name="Pharm A", slug="pharm-a")
        self.branch = Branch.objects.create(business=self.biz, name="Main", is_main=True)
        self.owner = self._user("owner", Membership.Role.OWNER)
        self.cashier = self._user("cashier", Membership.Role.CASHIER)

        self.panadol = Product.objects.create(business=self.biz, name="Panadol", selling_price=Decimal("2000"))
        self.amoxyl = Product.objects.create(business=self.biz, name="Amoxyl", selling_price=Decimal("6000"),
                                             requires_prescription=True)
        # Two batches: the one expiring sooner must be sold first.
        self.late = receive_stock(business=self.biz, branch=self.branch, product=self.panadol, quantity=10,
                                  cost_price=Decimal("1300"), expiry_date=self.today + timedelta(days=300))
        self.soon = receive_stock(business=self.biz, branch=self.branch, product=self.panadol, quantity=5,
                                  cost_price=Decimal("1000"), expiry_date=self.today + timedelta(days=30))
        receive_stock(business=self.biz, branch=self.branch, product=self.amoxyl, quantity=5,
                      cost_price=Decimal("3500"), expiry_date=self.today + timedelta(days=100))

        # A second business that must stay invisible to the first.
        other = Business.objects.create(name="Pharm B", slug="pharm-b")
        Product.objects.create(business=other, name="Secret product", selling_price=1)

    def _user(self, name, role):
        user = User.objects.create_user(username=name, password="pass12345!")
        Membership.objects.create(user=user, business=self.biz, role=role)
        return user

    def _sell(self, user, items, **extra):
        self.client.force_authenticate(user)
        return self.client.post("/api/v1/sales/", {"branch": self.branch.id, "items": items, **extra}, format="json")

    def test_sale_uses_first_expiry_first_and_records_profit(self):
        res = self._sell(self.owner, [{"product": self.panadol.id, "quantity": 7}])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Decimal(res.data["total"]), Decimal("14000"))
        # 5 from the soon batch at 1000 + 2 from the late batch at 1300 = 7600 cost
        self.assertEqual(Decimal(res.data["gross_profit"]), Decimal("14000") - Decimal("7600"))
        self.soon.refresh_from_db()
        self.late.refresh_from_db()
        self.assertEqual((self.soon.quantity_remaining, self.late.quantity_remaining), (0, 8))
        self.assertEqual(StockMovement.objects.filter(kind="sale").count(), 2)

    def test_cannot_oversell(self):
        res = self._sell(self.owner, [{"product": self.panadol.id, "quantity": 99}])
        self.assertEqual(res.status_code, 400)

    def test_cashier_cannot_sell_prescription_drug_or_see_profit(self):
        res = self._sell(self.cashier, [{"product": self.amoxyl.id, "quantity": 1}])
        self.assertEqual(res.status_code, 400)
        self.assertIn("prescription", str(res.data))
        res = self._sell(self.cashier, [{"product": self.panadol.id, "quantity": 1}])
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("gross_profit", res.data)

    def test_refund_returns_stock(self):
        sale_id = self._sell(self.owner, [{"product": self.panadol.id, "quantity": 3}]).data["id"]
        res = self.client.post(f"/api/v1/sales/{sale_id}/refund/", {"reason": "wrong item"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.panadol.stock_on_hand(), 15)

    def test_tenant_isolation(self):
        self.client.force_authenticate(self.owner)
        names = [p["name"] for p in self.client.get("/api/v1/products/").data["results"]]
        self.assertNotIn("Secret product", names)
        self.assertEqual(self.client.get("/api/v1/products/", HTTP_X_BUSINESS="pharm-b").status_code, 404)

    def test_public_store_needs_no_login_and_hides_costs(self):
        res = self.client.get("/api/v1/store/pharm-a/products/?search=pana")
        self.assertEqual(res.status_code, 200)
        product = res.data["results"][0]
        self.assertEqual(product["availability"]["status"], "in_stock")
        self.assertNotIn("cost_price", product)

    def test_dashboard_profit_and_losses(self):
        self._sell(self.owner, [{"product": self.panadol.id, "quantity": 2}])
        self.client.post(f"/api/v1/stock/batches/{self.late.id}/write-off/",
                         {"quantity": 1, "kind": "damaged", "reason": "dropped"}, format="json")
        data = self.client.get("/api/v1/reports/dashboard/").data
        self.assertEqual(data["sales"]["count"], 1)
        self.assertEqual(data["profit"]["gross_profit"], Decimal("2000"))  # 4000 - 2*1000
        self.assertEqual(data["profit"]["losses_written_off"], Decimal("1300"))
        self.client.force_authenticate(self.cashier)
        self.assertNotIn("profit", self.client.get("/api/v1/reports/dashboard/").data)
