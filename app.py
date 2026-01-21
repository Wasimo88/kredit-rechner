import streamlit as st
import math
import json
import time
from dataclasses import dataclass, field
from typing import List
from fpdf import FPDF
from datetime import datetime

# --- 0. SETUP & PASSWORT ---
APP_NAME = "Finanz-Suite Pro"

st.set_page_config(page_title=APP_NAME, page_icon="⚖️")

try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"]
except Exception:
    st.warning("⚠️ Kein Passwort konfiguriert. (Siehe Streamlit Secrets)")
    st.stop()

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

def check_password():
    if st.session_state.password_input == APP_PASSWORD:
        st.session_state.logged_in = True
    else:
        st.error("Zugriff verweigert.")

if not st.session_state.logged_in:
    st.title("🔒 Login erforderlich")
    st.text_input("Passwort:", type="password", key="password_input", on_change=check_password)
    st.button("Anmelden", on_click=check_password)
    st.stop()

# --- 1. DATEN & LOGIK ---

class BankPolicy:
    MIN_LIVING_COST_ADULT = 850.0
    MIN_LIVING_COST_PARTNER = 450.0
    MIN_LIVING_COST_CHILD = 350.0 
    MAX_DTI_PERCENT = 40.0 

    @staticmethod
    def get_dynamic_living_costs(net_income_household: float, has_partner: bool, children: int) -> float:
        base_need = BankPolicy.MIN_LIVING_COST_ADULT
        if has_partner:
            base_need += BankPolicy.MIN_LIVING_COST_PARTNER
        base_need += (children * BankPolicy.MIN_LIVING_COST_CHILD)
        dynamic_need = net_income_household * 0.35
        return max(base_need, dynamic_need)

@dataclass
class CustomerData:
    project_name: str
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
    interest_details: str
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
            errors.append("POLICY: Negative Schufa-Einträge vorhanden.")
        if c.employment_status == "probezeit" and amount > 5000:
            errors.append("POLICY: Während der Probezeit max. 5.000 € Darlehen möglich.")
        return errors

    @staticmethod
    def calculate_affordability(c: CustomerData) -> dict:
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
    def calculate_loan(c: CustomerData, amount: float, months: int, base_interest: float, use_scoring: bool) -> LoanResult:
        messages = []
        ko_errors = CreditDecisionEngine.check_hard_knockouts(c, amount)
        if ko_errors:
            return LoanResult(False, 0, 0, 0, "", 0, ko_errors)

        interest_rate = base_interest
        details_list = [f"{base_interest}% Basis"]
        
        if use_scoring:
            if c.employment_status == "befristet": 
                interest_rate += 1.5
                details_list.append("+ 1.5% (Befristet)")
            if c.employment_status == "selbststaendig": 
                interest_rate += 2.0
                details_list.append("+ 2.0% (Selbstständig)")
            if months > 84: 
                interest_rate += 0.5 
                details_list.append("+ 0.5% (Laufzeit > 7 J.)")
            
            if len(details_list) > 1:
                messages.append("Scoring aktiv: Risikoaufschläge wurden berechnet.")
            else:
                messages.append("Scoring aktiv: Keine Aufschläge notwendig.")
        else:
            messages.append("Fixzins gewählt (Keine Risikoaufschläge).")

        interest_rate = round(interest_rate, 2)
        interest_details_str = " ".join(details_list)
        
        rate = FinancialMath.calculate_rate(amount, months/12, interest_rate)
        rate = round(rate, 2)
        
        budget = CreditDecisionEngine.calculate_affordability(c)
        disposable = budget["disposable"]
        household_net = c.net_income + c.partner_income
        dti_current = (rate / household_net) * 100 if household_net > 0 else 0
        
        is_possible = True
        if disposable < rate:
            is_possible = False
            messages.append(f"NEGATIV: Rate ({rate}€) übersteigt das freie Budget ({disposable}€).")
        if dti_current > BankPolicy.MAX_DTI_PERCENT:
            is_possible = False
            messages.append(f"RISIKO: Verschuldungsquote (DTI) zu hoch ({dti_current:.1f}%).")

        total_repay = rate * months
        
        return LoanResult(
            approved=is_possible,
            max_loan_amount=amount,
            monthly_rate=rate,
            interest_rate=interest_rate,
            interest_details=interest_details_str,
            total_repayment=round(total_repay, 2),
            messages=messages,
            disposable_income=disposable,
            dti_ratio=dti_current
        )

# --- 2. PROFESSIONELLES PDF ---
class ProfessionalPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 18)
        self.cell(0, 10, 'FINANZIERUNGS-ERMITTLUNG', 0, 1, 'L')
        self.set_draw_color(100, 100, 100)
        self.set_line_width(0.5)
        self.line(10, 22, 200, 22)
        self.ln(15)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, f'Erstellt mit {APP_NAME} | Seite ' + str(self.page_no()), 0, 0, 'C')

    def chapter_title(self, label):
        self.set_font('Helvetica', 'B', 12)
        self.set_fill_color(240, 240, 240)
        self.set_text_color(0)
        self.cell(0, 8, f"  {label}", 0, 1, 'L', fill=True)
        self.ln(4)

    def chapter_row(self, label, value):
        self.set_font('Helvetica', '', 11)
        self.cell(110, 7, label, border='B')
        self.set_font('Helvetica', 'B', 11)
        self.cell(0, 7, value, border='B', ln=1, align='R')

    def create_report(self, res: LoanResult, c: CustomerData, amount, months):
        self.add_page()
        
        self.set_font('Helvetica', '', 11)
        self.cell(0, 6, f"Kunde / Projekt: {c.project_name}", 0, 1)
        self.cell(0, 6, f"Datum: {datetime.now().strftime('%d.%m.%Y')}", 0, 1)
        self.ln(8)
        
        self.chapter_title("1. KREDITANFRAGE")
        self.chapter_row("Gewünschter Nettokreditbetrag:", f"{amount:,.2f} EUR")
        self.chapter_row("Gewünschte Laufzeit:", f"{months} Monate")
        self.ln(8)
        
        self.set_font('Helvetica', 'B', 14)
        status_text = "FINANZIERUNG MÖGLICH" if res.approved else "NICHT MÖGLICH / ABGELEHNT"
        
        if res.approved:
            self.set_text_color(0, 128, 0)
            self.set_draw_color(0, 128, 0)
        else:
            self.set_text_color(200, 0, 0)
            self.set_draw_color(200, 0, 0)
            
        self.cell(0, 12, status_text, border=1, ln=1, align='C')
        self.set_text_color(0)
        self.set_draw_color(200)
        self.ln(8)
        
        self.chapter_title("2. ERMITTELTE KONDITIONEN")
        self.chapter_row("Monatliche Rate:", f"{res.monthly_rate:,.2f} EUR")
        self.chapter_row("Indikativer Zinssatz (p.a.):", f"{res.interest_rate} %")
        
        self.set_font('Helvetica', 'I', 9)
        self.set_text_color(80, 80, 80)
        self.cell(0, 6, f"Zusammensetzung: {res.interest_details}", 0, 1, 'R')
        self.set_text_color(0)
        self.set_font('Helvetica', '', 11)
        
        self.chapter_row("Gesamtrückzahlung:", f"{res.total_repayment:,.2f} EUR")
        self.ln(8)
        
        self.chapter_title("3. DETAILS ZUR BONITÄT")
        budget = CreditDecisionEngine.calculate_affordability(c)
        self.chapter_row("Anrechenbares Haushaltseinkommen:", f"{budget['income']:,.2f} EUR")
        self.chapter_row("Pauschale Lebenshaltungskosten:", f"- {budget['living_costs_assumed']:,.2f} EUR")
        self.chapter_row("Fixkosten & Verbindlichkeiten:", f"- {budget['expenses'] - budget['living_costs_assumed']:,.2f} EUR")
        
        self.ln(2)
        self.set_font('Helvetica', 'B', 11)
        self.cell(110, 8, "Frei verfügbares Budget:", border='T')
        self.cell(0, 8, f"{res.disposable_income:,.2f} EUR", border='T', ln=1, align='R')
        
        return bytes(self.output())

# --- 3. SPEICHERN & LADEN (CALLBACKS) ---

def get_session_data():
    """Sammelt Daten für den Download"""
    data = {
        "project_name": st.session_state.get("project_name", ""),
        "p_net": st.session_state.get("p_net", 2500),
        "p_has_part": st.session_state.get("p_has_part", False),
        "p_part_inc": st.session_state.get("p_part_inc", 0.0),
        "p_kids": st.session_state.get("p_kids", 0),
        "p_stat": st.session_state.get("p_stat", "fest"),
        "schufa_radio": st.session_state.get("schufa_radio", "Nein"),
        "p_rent_in": st.session_state.get("p_rent_in", 0.0),
        "p_other": st.session_state.get("p_other", 0.0),
        "p_rent_out": st.session_state.get("p_rent_out", 800.0),
        "p_mort": st.session_state.get("p_mort", 0.0),
        "p_loan": st.session_state.get("p_loan", 0.0),
        "p_save": st.session_state.get("p_save", 0.0),
        "p_amt": st.session_state.get("p_amt", 50000.0),
        "p_yrs": st.session_state.get("p_yrs", 10),
        "base_interest": st.session_state.get("base_interest", 4.0),
        "use_scoring": st.session_state.get("use_scoring", True)
    }
    return json.dumps(data, indent=4)

def import_callback():
    """Wird ausgeführt, BEVOR die Seite neu lädt. Verhindert den Instanzierungs-Fehler."""
    uploaded_file = st.session_state.get("upload_widget")
    if uploaded_file is not None:
        try:
            data = json.load(uploaded_file)
            for key, value in data.items():
                st.session_state[key] = value
            # Hinweis im State speichern, damit er nach dem Rerun angezeigt wird
            st.session_state["import_success"] = True
        except Exception as e:
            st.session_state["import_error"] = str(e)

# --- 4. UI HAUPTANWENDUNG ---
def main():
    st.title(f"💼 {APP_NAME}")
    
    project_name = st.text_input("Name des Kunden / Projekts", key="project_name", placeholder="z.B. Familie Müller")

    tab_simple, tab_pro = st.tabs(["🔢 Schnellrechner", "🏦 Profi-Analyse"])

    # Anzeige von Import-Nachrichten (nach Rerun)
    if st.session_state.get("import_success"):
        st.success("✅ Daten erfolgreich importiert!")
        st.session_state["import_success"] = False # Reset
    if st.session_state.get("import_error"):
        st.error(f"❌ Fehler beim Import: {st.session_state['import_error']}")
        st.session_state["import_error"] = None # Reset

    # TAB 1
    with tab_simple:
        st.subheader("Kredit-Schnellrechner")
        calc_mode = st.radio("Berechnungsmodus", ["Kreditrate berechnen (Summe gegeben)", "Kreditsumme berechnen (Rate gegeben)"])
        c1, c2, c3 = st.columns(3)
        amount_input = 0.0
        rate_input = 0.0
        
        if "Summe gegeben" in calc_mode:
            amount_input = c1.number_input("Kreditbetrag (€)", 10000, step=1000)
        else:
            rate_input = c1.number_input("Gewünschte Rate (€)", 300, step=50)
            
        years_simple = c2.number_input("Laufzeit (Jahre)", 1, 30, 5)
        interest_simple = c3.number_input("Zinssatz (%)", value=4.5, step=0.1)
        
        if st.button("Berechnen", type="primary", key="btn_simple"):
            st.divider()
            col_res1, col_res2 = st.columns(2)
            if "Summe gegeben" in calc_mode:
                rate_res = FinancialMath.calculate_rate(amount_input, years_simple, interest_simple)
                total_res = rate_res * years_simple * 12
                col_res1.metric("Monatliche Rate", f"{rate_res:,.2f} €")
                col_res2.metric("Gesamtkosten", f"{total_res:,.2f} €", delta=f"Zinsanteil: {total_res - amount_input:,.2f} €", delta_color="inverse")
            else:
                loan_res = FinancialMath.calculate_max_loan(rate_input, years_simple, interest_simple)
                total_res = rate_input * years_simple * 12
                col_res1.metric("Mögliche Kreditsumme", f"{loan_res:,.2f} €")
                col_res2.metric("Gesamtrückzahlung", f"{total_res:,.2f} €", delta=f"Kosten: {total_res - loan_res:,.2f} €", delta_color="inverse")

    # TAB 2
    with tab_pro:
        st.caption("Detaillierte Prüfung der Kapitaldienstfähigkeit")
        
        with st.expander("👤 1. Haushalt & Einkommen", expanded=True):
            col1, col2 = st.columns(2)
            net_income = col1.number_input("Monatliches Nettoeinkommen (€)", 2500, step=50, key="p_net")
            has_partner = col2.checkbox("Partner im Haushalt?", key="p_has_part")
            partner_income = 0.0
            if has_partner:
                partner_income = col2.number_input("Nettoeinkommen Partner (€)", 0, step=50, key="p_part_inc")
                st.success(f"Haushalts-Netto: {net_income + partner_income:,.2f} €")
            kids = st.slider("Anzahl Kinder im Haushalt", 0, 5, 0, key="p_kids")
            
            c3, c4 = st.columns(2)
            emp_status = c3.selectbox("Arbeitsverhältnis", ["fest", "befristet", "probezeit", "selbststaendig"], key="p_stat")
            schufa_select = c4.radio("Vorhandene Schufa-Einträge?", ["Nein", "Ja"], horizontal=True, key="schufa_radio")
            schufa_clean = True if schufa_select == "Nein" else False

        with st.expander("💰 2. Ausgaben & Verbindlichkeiten", expanded=False):
            c1, c2 = st.columns(2)
            rental = c1.number_input("Einnahmen aus Vermietung (Kalt)", 0, step=50, key="p_rent_in")
            other_inc = c1.number_input("Sonstige Einnahmen", 0, step=50, key="p_other")
            rent_warm = c2.number_input("Aktuelle Warmmiete", 800, step=50, key="p_rent_out")
            mortgage = c2.number_input("Rate für Immobilienfinanzierung", 0, step=50, key="p_mort")
            loans = c2.number_input("Rate für Konsumkredite (Auto etc.)", 0, step=50, key="p_loan")
            savings = c2.number_input("Monatliche Sparrate", 0, step=50, key="p_save")

        with st.expander("📊 3. Finanzierungswunsch", expanded=True):
            cw1, cw2, cw3 = st.columns(3)
            amount = cw1.number_input("Kreditsumme (€)", 50000, step=1000, key="p_amt")
            years = cw2.slider("Laufzeit (Jahre)", 1, 30, 10, key="p_yrs")
            base_interest = cw3.number_input("Basis-Zins (%)", value=4.0, step=0.1, key="base_interest")
            
        use_scoring = st.toggle("Automatisches Bank-Scoring (Risikoaufschläge)", value=True, key="use_scoring", help="Wenn aktiv, werden Aufschläge für Laufzeit (>7 Jahre) oder Risikoberufe auf den Basis-Zins addiert.")

        if st.button("Prüfung starten", type="primary", key="btn_pro"):
            customer = CustomerData(
                project_name=project_name if project_name else "Unbenannt",
                net_income=net_income, partner_income=partner_income,
                rental_income=rental, other_income=other_inc,
                rent_warm=rent_warm, mortgage_payment=mortgage,
                existing_loans=loans, savings_rate=savings,
                has_partner=has_partner, children_count=kids,
                employment_status=emp_status, schufa_clean=schufa_clean
            )
            
            result = CreditDecisionEngine.calculate_loan(customer, amount, years*12, base_interest, use_scoring)
            
            st.divider()
            c_res1, c_res2 = st.columns([2, 1])
            with c_res1:
                if result.approved:
                    st.subheader("✅ FINANZIERUNG MÖGLICH")
                    st.metric("Monatliche Rate", f"{result.monthly_rate:,.2f} €")
                    
                    if use_scoring and result.interest_rate > base_interest:
                         st.caption(f"Indikativer Zins: {result.interest_rate}% ({result.interest_details})")
                    else:
                         st.caption(f"Zinssatz: {result.interest_rate}% ({result.interest_details})")
                         
                else:
                    st.subheader("⚠️ NICHT MÖGLICH")
                    st.error("Die Kriterien für eine Kreditvergabe sind nicht erfüllt.")
            
            with c_res2:
                st.write("**Haushalts-Check:**")
                st.write(f"Frei: {result.disposable_income:,.2f} €")
                st.write(f"Auslastung: {result.dti_ratio:.1f}%")

            if result.messages:
                with st.container(border=True):
                    for msg in result.messages:
                        if "NEGATIV" in msg or "RISIKO" in msg or "POLICY" in msg:
                            st.write(f"❌ {msg}")
                        else:
                            st.write(f"ℹ️ {msg}")

            pdf = ProfessionalPDF()
            pdf_data = pdf.create_report(result, customer, amount, years*12)
            
            st.download_button(
                label="📄 PDF Bericht herunterladen",
                data=pdf_data,
                file_name=f"{project_name.replace(' ', '_')}_Finanz-Suite Pro.pdf",
                mime="application/pdf"
            )

    # --- DATENVERWALTUNG (UNTEN) ---
    st.divider()
    st.subheader("💾 Datenverwaltung")
    
    col_save, col_load = st.columns(2)
    
    with col_save:
        clean_name = project_name.replace(" ", "_") if project_name else "Unbenannt"
        filename_base = f"{APP_NAME.replace(' ', '_')}_{clean_name}"
        json_data = get_session_data()
        
        st.download_button(
            label="⬇️ Daten Speichern (JSON)",
            data=json_data,
            file_name=f"{filename_base}.json",
            mime="application/json",
            use_container_width=True
        )
        
    with col_load:
        # HIER IST DER FIX: Key und Callback
        uploaded_file = st.file_uploader("📂 Daten Laden", type=["json"], label_visibility="collapsed", key="upload_widget")
        
        # Der Button ruft jetzt die Callback-Funktion auf, BEVOR die Seite neu lädt
        if uploaded_file:
             st.button("⬆️ Importieren", use_container_width=True, on_click=import_callback)
    
    st.caption("Zum Speichern: 'Daten Speichern' klicken Zum Laden: JSON-Datei auswählen und 'Importieren' klicken.")

    st.divider()
    if st.button("🔒 Abmelden"):
        st.session_state.logged_in = False
        st.rerun()

if __name__ == "__main__":
    main()