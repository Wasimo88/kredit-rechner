import streamlit as st
import math
from dataclasses import dataclass, field
from typing import List, Tuple
from fpdf import FPDF

# --- 1. KONFIGURATION & BANK-REGELWERK ---
class BankPolicy:
    # Aktuelle Marktzinsen (Simuliert)
    BASE_RATE_CONSUMER = 3.9   # Konsumkredit Basis
    BASE_RATE_MORTGAGE = 3.5   # Immobilienkredit Basis
    
    # Maximale Verschuldungsquote (Rate darf max X% vom Netto sein)
    MAX_DTI_PERCENT = 40.0 
    
    # Pauschalen (Banken nutzen oft 60% des Einkommens für Lebenshaltung, aber mit Mindestsätzen)
    MIN_LIVING_COST_ADULT = 850.0
    MIN_LIVING_COST_PARTNER = 450.0
    MIN_LIVING_COST_CHILD = 350.0 # Orientierung Düsseldorfer Tabelle

    @staticmethod
    def get_dynamic_living_costs(net_income_household: float, has_partner: bool, children: int) -> float:
        """
        Banken-Logik: Wer viel verdient, hat höhere Ansprüche.
        Wir setzen hier eine dynamische Pauschale an, die mit dem Einkommen steigt,
        aber mindestens die festen Sätze abdeckt.
        """
        # Basisbedarf berechnen
        base_need = BankPolicy.MIN_LIVING_COST_ADULT
        if has_partner:
            base_need += BankPolicy.MIN_LIVING_COST_PARTNER
        base_need += (children * BankPolicy.MIN_LIVING_COST_CHILD)
        
        # Dynamischer Ansatz: Banken nehmen oft an, dass die Lebenshaltungskosten
        # mit dem Einkommen steigen (Lifestyle). Wir nehmen das Maximum aus Basisbedarf
        # ODER 35% des Haushaltseinkommens (damit reiche Kunden nicht "zu reich" gerechnet werden).
        dynamic_need = net_income_household * 0.35
        
        return max(base_need, dynamic_need)

# --- 2. DATENSTRUKTUREN ---
@dataclass
class CustomerData:
    net_income: float
    partner_income: float = 0.0 # WICHTIG: Volles Einkommen des Partners
    rental_income: float = 0.0
    other_income: float = 0.0
    
    rent_warm: float = 0.0
    mortgage_payment: float = 0.0
    existing_loans: float = 0.0
    savings_rate: float = 0.0
    
    has_partner: bool = False
    children_count: int = 0
    employment_status: str = "fest" # fest, befristet, probezeit, selbststaendig
    schufa_clean: bool = True       # Keine negativen Einträge

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

# --- 3. RECHENKERN (CORE BANKING LOGIC) ---
class CreditDecisionEngine:
    
    @staticmethod
    def check_hard_knockouts(c: CustomerData, amount: float) -> List[str]:
        """Prüft K.O.-Kriterien VOR der Berechnung"""
        errors = []
        if not c.schufa_clean:
            errors.append("ABTEILUNG RISIKO: Negative Schufa-Merkmale führen zur sofortigen Ablehnung.")
        
        if c.employment_status == "probezeit" and amount > 5000:
            errors.append("POLICY: In der Probezeit sind maximal 5.000 € möglich.")
            
        if c.net_income < 1300 and not c.has_partner:
            errors.append("POLICY: Einkommen liegt unter der Pfändungsfreigrenze (ca. 1.400€). Kreditvergabe risikoreich.")
            
        return errors

    @staticmethod
    def calculate_affordability(c: CustomerData) -> dict:
        """Erstellt die Haushaltsrechnung (Kapitaldienstfähigkeit)"""
        # 1. Einnahmen
        # Mieteinnahmen werden pauschal um 20% gekürzt (Bewirtschaftungskosten)
        adj_rental = c.rental_income * 0.80
        total_income = c.net_income + c.partner_income + adj_rental + c.other_income
        
        # 2. Ausgaben (Dynamische Lebenshaltungskosten)
        living_costs = BankPolicy.get_dynamic_living_costs(total_income, c.has_partner, c.children_count)
        
        # 3. Fixkosten
        housing_cost = c.rent_warm + c.mortgage_payment
        liabilities = c.existing_loans + c.savings_rate
        
        total_expenses = living_costs + housing_cost + liabilities
        
        # 4. Freies Budget
        disposable = total_income - total_expenses
        
        return {
            "income": total_income,
            "expenses": total_expenses,
            "disposable": round(disposable, 2),
            "living_costs_assumed": round(living_costs, 2)
        }

    @staticmethod
    def calculate_loan(c: CustomerData, amount: float, months: int) -> LoanResult:
        messages = []
        
        # A. K.O. Prüfung
        ko_errors = CreditDecisionEngine.check_hard_knockouts(c, amount)
        if ko_errors:
            return LoanResult(False, 0, 0, 0, 0, ko_errors)

        # B. Bonitäts-Zins ermitteln (Risk Pricing)
        interest_rate = BankPolicy.BASE_RATE_CONSUMER
        
        # Zinsaufschläge
        if c.employment_status == "befristet": interest_rate += 1.5
        if c.employment_status == "selbststaendig": interest_rate += 2.0
        
        # Laufzeitaufschlag (Zinsstrukturkurve simulieren: Längere Laufzeit = teurer)
        if months > 60: interest_rate += 0.4
        if months > 84: interest_rate += 0.8
        
        # C. Rate berechnen
        r_monthly = (interest_rate / 100) / 12
        rate = amount * (r_monthly * (1 + r_monthly)**months) / ((1 + r_monthly)**months - 1)
        rate = round(rate, 2)
        
        # D. Haushaltsprüfung
        budget = CreditDecisionEngine.calculate_affordability(c)
        disposable = budget["disposable"]
        
        # E. DTI Prüfung (Debt to Income)
        # Darf die Rate X% des Nettoeinkommens übersteigen?
        household_net = c.net_income + c.partner_income
        dti_current = (rate / household_net) * 100
        
        is_approved = True
        
        if disposable < rate:
            is_approved = False
            messages.append(f"ABLEHNUNG: Rate ({rate}€) ist höher als frei verfügbares Einkommen ({disposable}€).")
            
        if dti_current > BankPolicy.MAX_DTI_PERCENT:
            is_approved = False
            messages.append(f"RISIKO: Die Rate entspricht {dti_current:.1f}% Ihres Einkommens. Erlaubt sind max. {BankPolicy.MAX_DTI_PERCENT}%.")

        total_repay = rate * months
        
        return LoanResult(
            approved=is_approved,
            max_loan_amount=amount,
            monthly_rate=rate,
            interest_rate=round(interest_rate, 2),
            total_repayment=round(total_repay, 2),
            messages=messages,
            disposable_income=disposable,
            dti_ratio=dti_current
        )

# --- 4. PDF ENGINE ---
class BankPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 16)
        self.cell(0, 10, 'KREDITANFRAGE - PRÜFPROTOKOLL', 0, 1, 'C')
        self.ln(5)

    def create_report(self, res: LoanResult, c: CustomerData, amount, months):
        self.add_page()
        self.set_font('Helvetica', '', 11)
        
        self.cell(0, 8, f"Kreditsumme: {amount:,.2f} EUR | Laufzeit: {months} Monate", 0, 1)
        self.ln(5)
        
        # Status Box
        self.set_font('Helvetica', 'B', 14)
        status = "GENEHMIGT" if res.approved else "ABGELEHNT"
        color = (0, 150, 0) if res.approved else (200, 0, 0)
        self.set_text_color(*color)
        self.cell(0, 10, f"STATUS: {status}", 0, 1)
        self.set_text_color(0,0,0)
        self.ln(5)
        
        # Details
        self.set_font('Helvetica', '', 11)
        self.cell(95, 8, f"Monatliche Rate: {res.monthly_rate:,.2f} EUR")
        self.cell(95, 8, f"Zinssatz (eff. ca): {res.interest_rate} %", 0, 1)
        self.cell(95, 8, f"Gesamtrückzahlung: {res.total_repayment:,.2f} EUR")
        self.cell(95, 8, f"Zinskosten: {res.total_repayment - amount:,.2f} EUR", 0, 1)
        self.ln(5)
        
        self.set_font('Helvetica', 'B', 11)
        self.cell(0, 8, "Haushaltsrechnung (Interne Bankdaten):", 0, 1)
        self.set_font('Helvetica', '', 10)
        
        # Haushaltstabelle
        budget = CreditDecisionEngine.calculate_affordability(c)
        self.cell(100, 6, f"Gesamteinkommen Haushalt:", 0, 0)
        self.cell(50, 6, f"{budget['income']:,.2f} EUR", 0, 1, 'R')
        
        self.cell(100, 6, f"Angesetzte Lebenshaltung (Pauschale + Lifestyle):", 0, 0)
        self.cell(50, 6, f"- {budget['living_costs_assumed']:,.2f} EUR", 0, 1, 'R')
        
        self.cell(100, 6, f"Wohnkosten & Verpflichtungen:", 0, 0)
        self.cell(50, 6, f"- {budget['expenses'] - budget['living_costs_assumed']:,.2f} EUR", 0, 1, 'R')
        
        self.set_font('Helvetica', 'B', 10)
        self.cell(100, 8, f"Frei verfügbar (Kapitaldienstgrenze):", "T", 0)
        self.cell(50, 8, f"{res.disposable_income:,.2f} EUR", "T", 1, 'R')
        
        self.ln(5)
        if res.messages:
            self.set_text_color(200, 0, 0)
            self.cell(0, 8, "Meldungen / Ablehnungsgründe:", 0, 1)
            self.set_font('Helvetica', '', 9)
            for msg in res.messages:
                self.multi_cell(0, 5, f"- {msg}")
                
        return bytes(self.output())

# --- 5. FRONTEND (STREAMLIT) ---
def main():
    st.set_page_config(page_title="Bank Rating Tool", page_icon="🏦")
    st.title("🏦 Bank Rating Tool v2.0")
    st.caption("Professionelle Kapitaldienstfähigkeitsrechnung nach Bankstandards")

    # --- EINGABE MASKE ---
    with st.expander("👤 1. Persönliche Daten & Haushalt", expanded=True):
        col1, col2 = st.columns(2)
        net_income = col1.number_input("Dein Nettoeinkommen (€)", 2500, step=50)
        
        has_partner = col2.checkbox("Partner im Haushalt?")
        partner_income = 0.0
        if has_partner:
            partner_income = col2.number_input("Nettoeinkommen Partner (€)", 0, step=50)
            st.success(f"Haushalts-Netto: {net_income + partner_income:,.2f} €")
        
        kids = st.slider("Kinder im Haushalt", 0, 5, 0)
        
        c3, c4 = st.columns(2)
        emp_status = c3.selectbox("Arbeitsverhältnis", ["fest", "befristet", "probezeit", "selbststaendig"])
        schufa = c4.toggle("Schufa/Bonität einwandfrei?", value=True)
        if not schufa:
            st.error("Achtung: Negative Schufa führt meist zur direkten Ablehnung.")

    with st.expander("💰 2. Finanzielle Situation (Monatlich)", expanded=False):
        c1, c2 = st.columns(2)
        # Einnahmen
        rental = c1.number_input("Mieteinnahmen (Kalt)", 0, step=50)
        other_inc = c1.number_input("Sonstige Einnahmen (Kindergeld etc.)", 0, step=50)
        
        # Ausgaben
        rent_warm = c2.number_input("Aktuelle Warmmiete (fällt weg bei Hauskauf?)", 800, step=50)
        mortgage = c2.number_input("Bestehende Kreditraten Immobilien", 0, step=50)
        loans = c2.number_input("Bestehende Ratenkredite (Auto etc.)", 0, step=50)
        savings = c2.number_input("Feste Sparrate (die weiterlaufen soll)", 0, step=50)

    with st.expander("📊 3. Kreditwunsch", expanded=True):
        cw1, cw2 = st.columns(2)
        amount = cw1.number_input("Kreditsumme (€)", 20000, step=1000)
        years = cw2.slider("Laufzeit (Jahre)", 1, 20, 5)
        months = years * 12

    # --- BERECHNUNG ---
    if st.button("Bonität prüfen & Berechnen", type="primary"):
        # Daten Objekt bauen
        customer = CustomerData(
            net_income=net_income, partner_income=partner_income,
            rental_income=rental, other_income=other_inc,
            rent_warm=rent_warm, mortgage_payment=mortgage,
            existing_loans=loans, savings_rate=savings,
            has_partner=has_partner, children_count=kids,
            employment_status=emp_status, schufa_clean=schufa
        )
        
        # Engine anwerfen
        result = CreditDecisionEngine.calculate_loan(customer, amount, months)
        
        st.divider()
        
        # Ergebnis Darstellung
        c_res1, c_res2 = st.columns([2, 1])
        
        with c_res1:
            if result.approved:
                st.subheader("✅ KREDIT GENEHMIGT")
                st.metric("Monatliche Rate", f"{result.monthly_rate:,.2f} €")
                st.caption(f"Zinssatz: {result.interest_rate}% | Gesamtkosten: {result.total_repayment:,.2f} €")
            else:
                st.subheader("🚫 KREDIT ABGELEHNT")
                st.error("Die Kriterien der Bank wurden nicht erfüllt.")
        
        with c_res2:
            st.write("**Budget-Check:**")
            st.write(f"Frei: {result.disposable_income:,.2f} €")
            st.write(f"Belastung: {result.dti_ratio:.1f}% vom Netto")
            if result.dti_ratio > 40:
                st.caption("⚠️ Belastung zu hoch (>40%)")

        # Detaillierte Meldungen
        if result.messages:
            with st.container(border=True):
                st.write("**Analyse-Protokoll:**")
                for msg in result.messages:
                    if "ABLEHNUNG" in msg or "RISIKO" in msg or "POLICY" in msg:
                        st.write(f"❌ {msg}")
                    else:
                        st.write(f"ℹ️ {msg}")

        # PDF Download
        pdf = BankPDF()
        pdf_data = pdf.create_report(result, customer, amount, months)
        st.download_button("📄 Prüfprotokoll herunterladen (PDF)", data=pdf_data, file_name="Bank_Prufung.pdf", mime="application/pdf")

if __name__ == "__main__":
    main()
