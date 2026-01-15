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
    rental_income: float = 0.0          # NEU: Mieteinnahmen
    other_income: float = 0.0
    
    rent_warm: float = 0.0              # Eigene Miete (falls Mieter)
    mortgage_payment: float = 0.0       # NEU: Rate für eigene Immobilien
    consumer_loans: float = 0.0         # NEU: Ratenkredite (Auto, etc.)
    savings_rate: float = 0.0           # NEU: Feste Sparrate
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
        # Risikofaktoren Employment
        if customer.employment_status == "selbststaendig": rate += 1.5
        elif customer.employment_status == "befristet": rate += 0.8
        elif customer.employment_status == "probezeit": rate += 1.0
        
        # Risikofaktoren Schufa (Simulation)
        if customer.schufa_score_proxy < 90: rate += 2.0
        elif customer.schufa_score_proxy < 95: rate += 0.5
        elif customer.schufa_score_proxy > 98: rate -= 0.2
        
        # Bonus für Immobilienbesitz (Stabilität)
        if customer.rental_income > 0 or customer.mortgage_payment > 0:
            rate -= 0.1
            
        return round(rate, 2)

class HouseholdCalculator:
    @staticmethod
    def get_disposable_income(c: CustomerData) -> float:
        # 1. Einnahmen Seite (Mieteinnahmen pauschal 80% ansetzen)
        rental_income_adjusted = c.rental_income * 0.80 
        income_total = c.net_income + rental_income_adjusted + c.other_income
        
        # 2. Lebenshaltungspauschalen
        pauschale = PAUSCHALEN["lebenshaltung_erw"]
        if c.has_partner: pauschale += PAUSCHALEN["lebenshaltung_partner"]
        pauschale += (c.children_count * PAUSCHALEN["lebenshaltung_kind"])
        
        # 3. Fixkosten & Verpflichtungen
        housing_cost = c.rent_warm + c.mortgage_payment
        obligations = c.consumer_loans + c.savings_rate + c.expenses_misc
        
        # 4. Sicherheits-Puffer
        safety_buffer = (pauschale + housing_cost) * PAUSCHALEN["puffer_quote"]
        
        total_expenses = housing_cost + obligations + pauschale + safety_buffer
        
        return round(income_total - total_expenses, 2)

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
        # Zinssatz bestimmen
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

        # Berechnungslogik (Bidirektional)
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
        # Datum workaround für fpdf
        self.cell(0, 10, f"Persönliches Angebot", new_x="LMARGIN", new_y="NEXT")
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
        
        self.ln(10)
        self.set_font('Helvetica', 'I', 8)
        self.multi_cell(0, 5, "Dies ist ein unverbindliches Modellbeispiel. Die Kreditvergabe ist abhängig von einer finalen Bonitätsprüfung.")
        
        return bytes(self.output())

# --- STREAMLIT FRONTEND (Optimiert für Mobile) ---
def main():
    st.set_page_config(page_title="Profi Kreditrechner", page_icon="🏦")
    st.title("🏦 Allrounder Kreditrechner")

    # Tabs für bessere Übersicht
    tab1, tab2 = st.tabs(["🔢 Rechner", "👤 Kundendaten"])

    # --- TAB 2: KUNDENDATEN (Detailliert) ---
    with tab2:
        st.subheader("Detaillierte Haushaltsrechnung")
        
        # Sektion 1: Einnahmen
        with st.expander("💰 Einnahmen", expanded=True):
            col_inc1, col_inc2 = st.columns(2)
            net_income = col_inc1.number_input("Nettoeinkommen (€)", value=3200, step=50, help="Dein monatliches Netto")
            rental_inc = col_inc2.number_input("Mieteinnahmen (Kalt, €)", value=0, step=50, help="Einnahmen aus Immobilien")
            other_inc = st.number_input("Sonstige Einnahmen (€)", value=0, step=50)

        # Sektion 2: Wohnsituation & Kredite
        with st.expander("🏠 Wohnen & Verbindlichkeiten", expanded=False):
            col_exp1, col_exp2 = st.columns(2)
            rent_warm = col_exp1.number_input("Eigene Warmmiete (€)", value=0, step=50)
            mortgage = col_exp2.number_input("Eigene Kreditrate Immo (€)", value=0, step=50)
            
            st.caption("Weitere Verpflichtungen:")
            c_loans = st.number_input("Ratenkredite / Leasing (€)", value=0, step=10, help="Auto, Konsumkredite")
            savings = st.number_input("Feste Sparrate (€)", value=0, step=50, help="ETF, Bausparer etc.")

        # Sektion 3: Persönliches
        with st.expander("👤 Persönliche Situation", expanded=False):
            col_pers1, col_pers2 = st.columns(2)
            has_partner = col_pers1.checkbox("Partner im Haushalt?")
            kids = col_pers2.slider("Anzahl Kinder", 0, 5, 0)
            emp_status = st.selectbox("Arbeitsverhältnis", ["fest", "befristet", "selbststaendig", "probezeit"])
        
        # Objekt erstellen
        kunde = CustomerData(
            net_income=net_income, rental_income=rental_inc, other_income=other_inc,
            rent_warm=rent_warm, mortgage_payment=mortgage, consumer_loans=c_loans, savings_rate=savings,
            has_partner=has_partner, children_count=kids, employment_status=emp_status
        )
        
        verfuegbar = HouseholdCalculator.get_disposable_income(kunde)
        st.divider()
        if verfuegbar > 0:
            st.success(f"💎 Frei verfügbares Budget: {verfuegbar:,.2f} €")
        else:
            st.error(f"📉 Budget ausgeschöpft: {verfuegbar:,.2f} €")
        st.caption("*Mieteinnahmen werden zu 80% angerechnet.")

    # --- TAB 1: RECHNER ---
    with tab1:
        st.subheader("Was möchten Sie berechnen?")
        mode = st.radio("Modus:", ["Ich brauche eine Summe", "Ich habe eine Wunschrate"], horizontal=True)
        
        col_input, col_time = st.columns(2)
        wunschbetrag = 0.0
        wunschrate = 0.0
        
        if mode == "Ich brauche eine Summe":
            wunschbetrag = col_input.number_input("Kreditsumme (€)", value=45000, step=1000)
        else:
            wunschrate = col_input.number_input("Wunschrate (€)", value=500, step=50)
            
        years = col_time.slider("Laufzeit (Jahre)", 1, 30, 9)
        months = years * 12
        
        use_manual_interest = st.checkbox("Zinssatz manuell eingeben")
        manual_interest = None
        if use_manual_interest:
            manual_interest = st.number_input("Zinssatz (%)", value=4.5, step=0.1)

        if st.button("🚀 Berechnen", type="primary"):
            bank = BankLogic()
            offer = bank.create_loan_proposal(
                customer=kunde,
                desired_amount=wunschbetrag if wunschbetrag > 0 else None,
                desired_rate=wunschrate if wunschrate > 0 else None,
                duration_months=months,
                manual_interest=manual_interest
            )
            
            st.divider()
            m1, m2, m3 = st.columns(3)
            m1.metric("Kredit", f"{offer.request_amount:,.0f} €")
            m2.metric("Rate", f"{offer.monthly_rate:,.2f} €")
            m3.metric("Gesamt", f"{offer.total_repayment:,.0f} €", delta=f"-{offer.total_cost:,.0f} Zins", delta_color="inverse")
            
            if offer.is_feasible:
                st.success("✅ Machbar")
            else:
                st.error(f"❌ Budget reicht nicht")
                
            with st.expander("Details & PDF"):
                for msg in offer.messages:
                    st.write(f"- {msg}")
                st.write(f"Effektiver Jahreszins: {offer.effective_interest}%")
                
                exporter = LoanPDFExporter()
                pdf_data = exporter.create_offer_pdf(offer)
                st.download_button("📄 PDF speichern", data=pdf_data, file_name="Angebot.pdf", mime="application/pdf")

if __name__ == "__main__":
    main()