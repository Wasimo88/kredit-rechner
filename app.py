import streamlit as st
import math
from dataclasses import dataclass, field
from typing import List
from fpdf import FPDF
import io

# --- KONFIGURATION (Bank Standards) ---
PAUSCHALEN = {
    "lebenshaltung_erw": 850.0,
    "lebenshaltung_partner": 400.0,
    "lebenshaltung_kind": 300.0,
    "puffer_quote": 0.10,
}

# --- DATENSTRUKTUREN ---
@dataclass
class CustomerData:
    net_income: float
    other_income: float = 0.0
    rent_warm: float = 0.0
    other_loans: float = 0.0
    expenses_misc: float = 0.0
    has_partner: bool = False
    children_count: int = 0
    employment_status: str = "fest"
    schufa_score_proxy: int = 98

@dataclass
class LoanOffer:
    request_amount: float
    duration_months: int
    nominal_interest: float
    effective_interest: float
    monthly_rate: float
    total_repayment: float  # Gesamtsumme (Rate * Laufzeit)
    total_cost: float       # Zinskosten (Gesamt - Kreditbetrag)
    is_feasible: bool
    max_possible_rate: float
    messages: List[str] = field(default_factory=list)

# --- LOGIK KLASSEN (BACKEND) ---
class RiskEngine:
    BASE_RATE = 3.5

    @staticmethod
    def calculate_risk_adjusted_rate(customer: CustomerData) -> float:
        rate = RiskEngine.BASE_RATE
        if customer.employment_status == "selbststaendig": rate += 1.5
        elif customer.employment_status == "befristet": rate += 0.8
        elif customer.employment_status == "probezeit": rate += 1.0
        
        if customer.schufa_score_proxy < 90: rate += 2.0
        elif customer.schufa_score_proxy < 95: rate += 0.5
        elif customer.schufa_score_proxy > 98: rate -= 0.2
        return round(rate, 2)

class HouseholdCalculator:
    @staticmethod
    def get_disposable_income(c: CustomerData) -> float:
        income_total = c.net_income + c.other_income
        pauschale = PAUSCHALEN["lebenshaltung_erw"]
        if c.has_partner: pauschale += PAUSCHALEN["lebenshaltung_partner"]
        pauschale += (c.children_count * PAUSCHALEN["lebenshaltung_kind"])
        
        fix_costs = c.rent_warm + c.other_loans + c.expenses_misc
        safety_buffer = (pauschale + fix_costs) * PAUSCHALEN["puffer_quote"]
        
        return round(income_total - fix_costs - pauschale - safety_buffer, 2)

class BankLogic:
    @staticmethod
    def calculate_effective_rate(nominal_rate_percent, months, loan_amount):
        if loan_amount <= 0 or months <= 0: return 0.0
        monthly_nominal = (nominal_rate_percent / 100) / 12
        monthly_payment = loan_amount * (monthly_nominal * (1 + monthly_nominal)**months) / ((1 + monthly_nominal)**months - 1)
        
        # Vereinfachte Newton-Iteration für Effektivzins
        guess = monthly_nominal
        for _ in range(10):
            npv = loan_amount
            derivative = 0
            for i in range(1, months + 1):
                factor = (1 + guess) ** -i
                npv -= monthly_payment * factor
                derivative += monthly_payment * i * ((1 + guess) ** (-i - 1))
            if abs(derivative) < 1e-9: break
            guess = guess - (npv / derivative)
            
        return round(((1 + guess) ** 12 - 1) * 100, 2)

    def create_loan_proposal(self, customer: CustomerData, desired_amount=None, desired_rate=None, duration_months=48, manual_interest=None):
        # Zinssatz bestimmen (Manuell oder Risiko-Modell)
        if manual_interest:
            nominal_interest = manual_interest
        else:
            nominal_interest = RiskEngine.calculate_risk_adjusted_rate(customer)
            
        r_monthly = (nominal_interest / 100) / 12
        max_disposable_rate = HouseholdCalculator.get_disposable_income(customer)
        
        messages = []
        if not manual_interest:
            messages.append(f"Bonitäts-Zins ermittelt: {nominal_interest}% (Basis: {RiskEngine.BASE_RATE}%)")
        
        calculated_rate = 0.0
        final_loan_amount = 0.0

        # Berechnungslogik
        if desired_amount:
            final_loan_amount = desired_amount
            if r_monthly > 0:
                factor = (1 + r_monthly) ** duration_months
                calculated_rate = final_loan_amount * (r_monthly * factor) / (factor - 1)
            else:
                calculated_rate = final_loan_amount / duration_months
        elif desired_rate:
            calculated_rate = desired_rate
            if r_monthly > 0:
                factor = (1 + r_monthly) ** duration_months
                final_loan_amount = desired_rate * ((factor - 1) / (r_monthly * factor))
            else:
                final_loan_amount = desired_rate * duration_months

        # Ergebnisse zusammenstellen
        total_repayment = calculated_rate * duration_months
        total_cost = total_repayment - final_loan_amount
        eff_zins = self.calculate_effective_rate(nominal_interest, duration_months, final_loan_amount)

        is_feasible = max_disposable_rate >= calculated_rate
        if not is_feasible:
            messages.append(f"Budgetwarnung: Rate ({calculated_rate:.2f}€) übersteigt Verfügbares ({max_disposable_rate:.2f}€)")

        return LoanOffer(
            request_amount=round(final_loan_amount, 2),
            duration_months=duration_months,
            nominal_interest=nominal_interest,
            effective_interest=eff_zins,
            monthly_rate=round(calculated_rate, 2),
            total_repayment=round(total_repayment, 2),
            total_cost=round(total_cost, 2),
            is_feasible=is_feasible,
            max_possible_rate=max_disposable_rate,
            messages=messages
        )

# --- PDF EXPORT KLASSE ---
class LoanPDFExporter(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 15)
        self.cell(0, 10, 'Finanzierungsangebot / Tilgungsplan', border=False, new_x="LMARGIN", new_y="NEXT", align='C')
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Seite {self.page_no()}', align='C')

    def create_offer_pdf(self, offer: LoanOffer, customer_name="Kunde"):
        self.add_page()
        self.set_font('Helvetica', '', 12)
        self.cell(0, 10, f"Erstellt am: {st.session_state.get('today', 'Heute')} für {customer_name}", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)
        
        self.set_font('Helvetica', 'B', 12)
        self.cell(0, 10, "Konditionen:", new_x="LMARGIN", new_y="NEXT")
        self.set_font('Helvetica', '', 12)
        
        data = [
            ("Nettodarlehensbetrag:", f"{offer.request_amount:,.2f} EUR"),
            ("Laufzeit:", f"{offer.duration_months} Monate"),
            ("Sollzins (gebunden):", f"{offer.nominal_interest} %"),
            ("Effektiver Jahreszins:", f"{offer.effective_interest} %"),
            ("Monatliche Rate:", f"{offer.monthly_rate:,.2f} EUR"),
        ]
        
        for label, value in data:
            self.cell(90, 8, label)
            self.cell(0, 8, value, new_x="LMARGIN", new_y="NEXT")

        self.ln(5)
        self.set_text_color(200, 0, 0)
        self.set_font('Helvetica', 'B', 12)
        self.cell(90, 10, "Gesamtrückzahlungsbetrag:")
        self.cell(0, 10, f"{offer.total_repayment:,.2f} EUR", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0,0,0)
        self.set_font('Helvetica', '', 10)
        self.cell(90, 8, "Darin enthaltene Zinskosten:")
        self.cell(0, 8, f"{offer.total_cost:,.2f} EUR", new_x="LMARGIN", new_y="NEXT")
        
        return bytes(self.output())

# --- STREAMLIT FRONTEND ---
def main():
    st.set_page_config(page_title="Profi Kreditrechner", page_icon="🏦")
    st.title("🏦 Allrounder Kreditrechner")

    # Tabs für bessere Übersicht
    tab1, tab2 = st.tabs(["🔢 Rechner", "👤 Kundendaten (Optional)"])

    with tab2:
        st.subheader("Haushaltsrechnung & Bonität")
        c1, c2 = st.columns(2)
        net_income = c1.number_input("Nettoeinkommen (€)", value=3000, step=50)
        rent_warm = c2.number_input("Warmmiete (€)", value=900, step=50)
        has_partner = st.checkbox("Partner im Haushalt?")
        kids = st.slider("Anzahl Kinder", 0, 5, 0)
        emp_status = st.selectbox("Arbeitsverhältnis", ["fest", "befristet", "selbststaendig", "probezeit"])
        
        # Kunde Objekt erstellen
        kunde = CustomerData(
            net_income=net_income, rent_warm=rent_warm, 
            has_partner=has_partner, children_count=kids, employment_status=emp_status
        )
        verfuegbar = HouseholdCalculator.get_disposable_income(kunde)
        st.info(f"Kalkulatorisches freies Budget: {verfuegbar:,.2f} €")

    with tab1:
        st.subheader("Was möchten Sie berechnen?")
        mode = st.radio("Modus:", ["Ich brauche eine bestimmte Summe", "Ich habe eine Wunschrate"], horizontal=True)
        
        col_input, col_time = st.columns(2)
        
        wunschbetrag = 0.0
        wunschrate = 0.0
        
        if mode == "Ich brauche eine bestimmte Summe":
            wunschbetrag = col_input.number_input("Gewünschte Kreditsumme (€)", value=45000, step=1000)
        else:
            wunschrate = col_input.number_input("Monatliche Wunschrate (€)", value=500, step=50)
            
        years = col_time.slider("Laufzeit (Jahre)", 1, 30, 9)
        months = years * 12
        
        # Checkbox für manuellen Zins
        use_manual_interest = st.checkbox("Ich kenne meinen Zinssatz bereits")
        manual_interest = None
        if use_manual_interest:
            manual_interest = st.number_input("Zinssatz (%)", value=4.5, step=0.1)

        if st.button("🚀 Berechnen", type="primary"):
            bank = BankLogic()
            
            # Berechnung starten
            offer = bank.create_loan_proposal(
                customer=kunde,
                desired_amount=wunschbetrag if wunschbetrag > 0 else None,
                desired_rate=wunschrate if wunschrate > 0 else None,
                duration_months=months,
                manual_interest=manual_interest
            )
            
            st.divider()
            
            # Ergebnis Metriken
            m1, m2, m3 = st.columns(3)
            m1.metric("Kreditsumme", f"{offer.request_amount:,.2f} €")
            m2.metric("Monatliche Rate", f"{offer.monthly_rate:,.2f} €")
            m3.metric("Gesamtrückzahlung", f"{offer.total_repayment:,.2f} €", delta=f"-{offer.total_cost:,.2f} Zinsen", delta_color="inverse")
            
            if offer.is_feasible:
                st.success("✅ Finanzierung ist laut Haushaltsrechnung machbar.")
            else:
                st.error(f"❌ Achtung: Budget ({offer.max_possible_rate}€) reicht evtl. nicht für Rate ({offer.monthly_rate}€).")
                
            with st.expander("Details ansehen"):
                for msg in offer.messages:
                    st.write(f"- {msg}")
                st.write(f"Effektiver Jahreszins: {offer.effective_interest}%")

            # PDF Download
            exporter = LoanPDFExporter()
            pdf_data = exporter.create_offer_pdf(offer)
            st.download_button("📄 PDF Angebot herunterladen", data=pdf_data, file_name="Kreditangebot.pdf", mime="application/pdf")

if __name__ == "__main__":
    main()
