import { useState } from "react";
import { initials, type Account } from "../auth/auth";

/** The person's Google or Facebook photo, or their initials when there is none or it fails to load (Facebook
    photo links expire; the next session refresh brings a fresh one). Decorative: the name is always written beside it. */
export default function Avatar({ account, size }: { account: Pick<Account, "name" | "avatar">; size?: "lg" }) {
  const [broken, setBroken] = useState<string | null>(null);
  const photo = account.avatar && account.avatar !== broken ? account.avatar : null;
  return (
    <span className={`avatar${size ? ` ${size}` : ""}${photo ? " photo" : ""}`} aria-hidden="true">
      {photo ? (
        <img src={photo} alt="" referrerPolicy="no-referrer" decoding="async" onError={() => setBroken(photo)} />
      ) : (
        initials(account.name)
      )}
    </span>
  );
}
