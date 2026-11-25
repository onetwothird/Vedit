import { Link } from "react-router-dom";
import Logo from "../components/Logo";
import { t } from "../i18n/t";
import "./legal.css";

const SECTIONS = {
  terms: ["use", "content", "rules", "limits", "takedown", "warranty"],
  privacy: ["collect", "use", "services", "delete", "contact"],
} as const;

/** Where takedown and privacy requests go: set per deployment, never invented here. */
function contactEmail(): string {
  const v = import.meta.env.VITE_CONTACT_EMAIL;
  return typeof v === "string" ? v.trim() : "";
}

/** The legal pages are templates: they say so on the page until someone has reviewed them. */
export default function Legal({ page }: { page: "terms" | "privacy" }) {
  const email = contactEmail();
  return (
    <div className="legal">
      <header className="legal-top">
        <Link className="legal-brand" to="/" aria-label={t("legal.back")}>
          <Logo size={24} />
          <span>{t("app.name")}</span>
        </Link>
        <nav aria-label={t("legal.nav")}>
          <Link to="/terms" aria-current={page === "terms" ? "page" : undefined}>{t("legal.terms.link")}</Link>
          <Link to="/privacy" aria-current={page === "privacy" ? "page" : undefined}>{t("legal.privacy.link")}</Link>
        </nav>
      </header>
      <main className="legal-body">
        <p className="legal-draft" role="note"><strong>{t("legal.draft")}</strong> {t("legal.draft.note")}</p>
        <h1>{t(`${page}.title`)}</h1>
        {SECTIONS[page].map((s) => (
          <section key={s}>
            <h2>{t(`${page}.${s}.h`)}</h2>
            <p>{t(`${page}.${s}.p`)}</p>
          </section>
        ))}
        <section>
          <h2>{t("legal.contact.label")}</h2>
          <p className="legal-contact">
            {email ? <a href={`mailto:${email}`}>{email}</a> : <span className="legal-missing">{t("legal.contact.unset")}</span>}
          </p>
        </section>
        <p><Link to="/">{t("legal.back")}</Link></p>
      </main>
    </div>
  );
}
