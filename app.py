import streamlit as st
import math
from dataclasses import dataclass, field
from typing import List
from fpdf import FPDF

# --- 1. KONFIGURATION & LOGIK ---

class BankPolicy:
    # Pauschalen (Banken-Standard)
    MIN_LIVING_COST_ADULT = 850.0
    MIN_LIVING_COST_PARTNER = 450.0
    MIN_LIVING_COST_CHILD = 350.0 
    MAX_DTI_PERCENT = 40.0 

    @staticmethod
    def get_dynamic_living_costs(net_income_household: float, has_partner: bool, children: int) -> float:
        # Basisbedarf
        base_need = BankPolicy.MIN_LIVING_COST_ADULT
        if has_partner:
            base_need += BankPolicy.MIN_LIVING_COST_PARTNER
        base_need += (children * BankPolicy.MIN_LIVING_COST_CHILD)
        
        # Lifestyle-Pauschale: Wer mehr verdient, gibt mehr aus (max 35% vom Netto)
        dynamic_need = net_income_household * 0.35
        return max(base_need, dynamic_need)

@dataclass
class CustomerData:
    net_income: float
    partner_income: float = 0.0
    rental_income: float = 0.0
    other_income: float = 0.0
    rent_warm: float = 0.0
    mortgage_payment: float = 0.0
    existing_loans: float = 0.0
    savings_rate: float = 0.0
    has_partner: bool = False
    children_count: int = 0
    employment_status: str = "fest"
    schufa_clean: bool = True

@dataclass
class LoanResult:
    approved: bool
    max_loan_amount: float
    monthly_rate: float
    interest_rate: float
    total_repayment: float
    messages: List[str] = field(default_factory=list)
    disposable_income: float = 0.0
    dti_ratio: float = 0.0

class FinancialMath:
    @staticmethod
    def calculate_rate(amount, years, interest_percent):
        if interest_percent == 0: return amount / (years * 12)
        r_monthly = (interest_percent / 100) / 12
        months = years * 12
        factor = (1 + r_monthly) ** months
        return amount * (r_monthly * factor) / (factor - 1)

    @staticmethod
    def calculate_max_loan(target_rate, years, interest_percent):
        if interest_percent == 0: return target_rate * years * 12
        r_monthly = (interest_percent / 100) / 12
        months = years * 12
        factor = (1 + r_monthly) ** months
        return target_rate * ((factor - 1) / (r_monthly * factor))

class CreditDecisionEngine:
    @staticmethod
    def check_hard_knockouts(c: CustomerData, amount: float) -> List[str]:
        errors = []
        if not c.schufa_clean:
            errors.append("POLICY: Negative Schufa-Merkmale.")
        if c.employment_status == "probezeit" and amount > 5000:
            errors.append("POLICY: In Probezeit max. 5.000 €.")
        return errors

    @staticmethod
    def calculate_affordability(c: CustomerData) -> dict:
        # Mieteinnahmen pauschal 80%
        adj_rental = c.rental_income * 0.80
        total_income = c.net_income + c.partner_income + adj_rental + c.other_income
        
        living_costs = BankPolicy.get_dynamic_living_costs(total_income, c.has_partner, c.children_count)
        
        housing_cost = c.rent_warm + c.mortgage_payment
        liabilities = c.existing_loans + c.savings_rate
        total_expenses = living_costs + housing_cost + liabilities
        
        return {
            "income": total_income,
            "expenses": total_expenses,
            "disposable": round(total_income - total_expenses, 2),
            "living_costs_assumed": round(living_costs, 2)
        }

    @staticmethod
    def calculate_loan(c: CustomerData, amount: float, months: int, base_interest: float) -> LoanResult:
        messages = []
        ko_errors = CreditDecisionEngine.check_hard_knockouts(c, amount)
        if ko_errors:
            return LoanResult(False, 0, 0, 0, 0, ko_errors)

        # Zinssatz ermitteln (Basis + Risikoaufschlag)
        interest_rate = base_interest
        if c.employment_status == "befristet": interest_rate += 1.5
        if c.employment_status == "selbststaendig": interest_rate += 2.0
        if months > 84: interest_rate += 0.5 # Laufzeitaufschlag
        
        interest_rate = round(interest_rate, 2)
        
        # Rate berechnen
        rate = FinancialMath.calculate_rate(amount, months/12, interest_rate)
        rate = round(rate, 2)
        
        # Prüfungen
        budget = CreditDecisionEngine.calculate_affordability(c)
        disposable = budget["disposable"]
        household_net = c.net_income + c.partner_income
        dti_current = (rate / household_net) * 100 if household_net > 0 else 0
        
        is_possible = True
        if disposable < rate:
            is_possible = False
            messages.append(f"NEGATIV: Rate ({rate}€) höher als freies Budget ({disposable}€).")
        if dti_current > BankPolicy.MAX_DTI_PERCENT:
            is_possible = False
            messages.append(f"RISIKO: DTI Quote zu hoch ({dti_current:.1f}%).")

        total_repay = rate * months
        
        return LoanResult(
            approved=is_possible,
            max_loan_amount=amount,
            monthly_rate=rate,
            interest_rate=interest_rate,
            total_repayment=round(total_repay, 2),
            messages=messages,
            disposable_income=disposable,
            dti_ratio=dti_current
        )

# --- 2. PDF ENGINE ---
class BankPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 16)
        self.cell(0, 10, 'FINANZIERUNGS-PRÜFUNG', 0, 1, 'C')
        self.ln(5)

    def create_report(self, res: LoanResult, c: CustomerData, amount, months):
        self.add_page()
        self.set_font('Helvetica', '', 11)
        self.cell(0, 8, f"Kreditsumme: {amount:,.2f} EUR | Laufzeit: {months} Monate", 0, 1)
        self.ln(5)
        
        self.set_font('Helvetica', 'B', 14)
        status = "FINANZIERUNG MÖGLICH" if res.approved else "NICHT MÖGLICH"
        color = (0, 150, 0) if res.approved else (200, 0, 0)
        self.set_text_color(*color)
        self.cell(0, 10, f"ERGEBNIS: {status}", 0, 1)
        self.set_text_color(0,0,0)
        self.ln(5)
        
        self.set_font('Helvetica', '', 11)
        self.cell(95, 8, f"Monatliche Rate: {res.monthly_rate:,.2f} EUR")
        self.cell(95, 8, f"Kalkulierter Zins: {res.interest_rate} %", 0, 1)
        self.cell(95, 8, f"Gesamtrückzahlung: {res.total_repayment:,.2f} EUR")
        self.ln(5)
        
        self.set_font('Helvetica', 'B', 11)
        self.cell(0, 8, "Details zur Haushaltsrechnung:", 0, 1)
        self.set_font('Helvetica', '', 10)
        budget = CreditDecisionEngine.calculate_affordability(c)
        self.cell(100, 6, f"Haushalts-Einkommen (angerechnet):", 0, 0)
        self.cell(50, 6, f"{budget['income']:,.2f} EUR", 0, 1, 'R')
        self.cell(100, 6, f"Frei verfügbar:", 0, 0)
        self.cell(50, 6, f"{res.disposable_income:,.2f} EUR", 0, 1, 'R')
        
        return bytes(self.output())

# --- 3. FRONTEND (STREAMLIT) ---
def main():
    st.set_page_config(page_title="Finanz-Suite V3", page_icon="💶")
    st.title("💶 Finanz-Suite")

    # HAUPT-NAVIGATION
    tab_simple, tab_pro = st.tabs(["🔢 Einfacher Rechner", "🏦 Profi Bank-Check"])

    # ---------------------------------------------------------
    # TAB 1: EINFACHER RECHNER (Die "Allrounder" Funktion)
    # ---------------------------------------------------------
    with tab_simple:
        st.subheader("Schnell-Kalkulation")
        calc_mode = st.radio("Was berechnen?", ["Ich habe eine Summe", "Ich habe eine Wunschrate"], horizontal=True)
        
        c1, c2, c3 = st.columns(3)
        
        amount_input = 0.0
        rate_input = 0.0
        
        if calc_mode == "Ich habe eine Summe":
            amount_input = c1.number_input("Kreditbetrag (€)", 10000, step=1000)
        else:
            rate_input = c1.number_input("Wunschrate (€)", 300, step=50)
            
        years_simple = c2.number_input("Laufzeit (Jahre)", 1, 30, 5)
        interest_simple = c3.number_input("Zinssatz (%)", value=4.5, step=0.1)
        
        if st.button("Rechnen", type="primary", key="btn_simple"):
            st.divider()
            col_res1, col_res2 = st.columns(2)
            
            if calc_mode == "Ich habe eine Summe":
                # Wir suchen die Rate
                rate_res = FinancialMath.calculate_rate(amount_input, years_simple, interest_simple)
                total_res = rate_res * years_simple * 12
                interest_cost = total_res - amount_input
                
                col_res1.metric("Monatliche Rate", f"{rate_res:,.2f} €")
                col_res2.metric("Gesamtkosten", f"{total_res:,.2f} €", delta=f"davon {interest_cost:,.2f} € Zinsen", delta_color="inverse")
            else:
                # Wir suchen die Summe
                loan_res = FinancialMath.calculate_max_loan(rate_input, years_simple, interest_simple)
                total_res = rate_input * years_simple * 12
                interest_cost = total_res - loan_res
                
                col_res1.metric("Mögliche Kreditsumme", f"{loan_res:,.2f} €")
                col_res2.metric("Gesamtrückzahlung", f"{total_res:,.2f} €", delta=f"Kosten: {interest_cost:,.2f} €", delta_color="inverse")

    # ---------------------------------------------------------
    # TAB 2: PROFI BANK-CHECK (Die detaillierte Prüfung)
    # ---------------------------------------------------------
    with tab_pro:
        st.caption("Detaillierte Prüfung der Bonität und Haushaltsrechnung")
        
        with st.expander("👤 1. Haushalt & Einkommen", expanded=True):
            col1, col2 = st.columns(2)
            net_income = col1.number_input("Dein Nettoeinkommen (€)", 2500, step=50, key="p_net")
            has_partner = col2.checkbox("Partner im Haushalt?", key="p_has_part")
            partner_income = 0.0
            if has_partner:
                partner_income = col2.number_input("Netto Partner (€)", 0, step=50, key="p_part_inc")
                st.success(f"Haushalts-Netto: {net_income + partner_income:,.2f} €")
            kids = st.slider("Kinder", 0, 5, 0, key="p_kids")
            
            c3, c4 = st.columns(2)
            emp_status = c3.selectbox("Status", ["fest", "befristet", "probezeit", "selbststaendig"], key="p_stat")
            schufa = c4.toggle("Schufa sauber?", value=True, key="p_schufa")

        with st.expander("💰 2. Ausgaben & Verbindlichkeiten", expanded=False):
            c1, c2 = st.columns(2)
            rental = c1.number_input("Mieteinnahmen (Kalt)", 0, step=50, key="p_rent_in")
            other_inc = c1.number_input("Sonstige Einnahmen", 0, step=50, key="p_other")
            rent_warm = c2.number_input("Aktuelle Warmmiete", 800, step=50, key="p_rent_out")
            mortgage = c2.number_input("Kreditraten Immo", 0, step=50, key="p_mort")
            loans = c2.number_input("Ratenkredite (Auto)", 0, step=50, key="p_loan")
            savings = c2.number_input("Feste Sparrate", 0, step=50, key="p_save")

        with st.expander("📊 3. Kreditwunsch & Markt", expanded=True):
            cw1, cw2, cw3 = st.columns(3)
            amount = cw1.number_input("Kreditsumme (€)", 50000, step=1000, key="p_amt")
            years = cw2.slider("Laufzeit (Jahre)", 1, 25, 10, key="p_yrs")
            # NEU: Variabler Zins
            base_interest = cw3.number_input("Basis-Zins aktuell (%)", value=4.0, step=0.1, help="Aktueller Marktzins, auf den Risikozuschläge addiert werden.")

        if st.button("Bonität prüfen", type="primary", key="btn_pro"):
            customer = CustomerData(
                net_income=net_income, partner_income=partner_income,
                rental_income=rental, other_income=other_inc,
                rent_warm=rent_warm, mortgage_payment=mortgage,
                existing_loans=loans, savings_rate=savings,
                has_partner=has_partner, children_count=kids,
                employment_status=emp_status, schufa_clean=schufa
            )
            
            # Hier übergeben wir den variablen Zins an die Engine
            result = CreditDecisionEngine.calculate_loan(customer, amount, years*12, base_interest)
            
            st.divider()
            c_res1, c_res2 = st.columns([2, 1])
            
            with c_res1:
                # Wording angepasst
                if result.approved:
                    st.subheader("✅ FINANZIERUNG MÖGLICH")
                    st.metric("Kalkulierte Rate", f"{result.monthly_rate:,.2f} €")
                    st.caption(f"Zinssatz (Indikativ): {result.interest_rate}% (Basis {base_interest}% + Risiko)")
                else:
                    st.subheader("⚠️ KRITISCH / NICHT MÖGLICH")
                    st.error("Kriterien der Bank-Logik nicht erfüllt.")
            
            with c_res2:
                st.write("**Budget-Check:**")
                st.write(f"Frei: {result.disposable_income:,.2f} €")
                st.write(f"DTI: {result.dti_ratio:.1f}%")
                if result.dti_ratio > 40:
                    st.caption("⚠️ Quote > 40%")

            if result.messages:
                with st.container(border=True):
                    for msg in result.messages:
                        if "NEGATIV" in msg or "RISIKO" in msg or "POLICY" in msg:
                            st.write(f"❌ {msg}")
                        else:
                            st.write(f"ℹ️ {msg}")

            pdf = BankPDF()
            pdf_data = pdf.create_report(result, customer, amount, years*12)
            st.download_button("📄 Prüfprotokoll (PDF)", data=pdf_data, file_name="Finanzpruefung.pdf", mime="application/pdf")

if __name__ == "__main__":
    main()
