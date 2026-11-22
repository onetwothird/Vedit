//! AI provider keys in the OS credential store (Windows Credential Manager): service "vedit", user "ai-<provider>".
//! Keys are never logged; errors carry only a code and the keyring error kind.

use keyring::{Entry, Error};

const SERVICE: &str = "vedit";
pub const INVALID_PROVIDER: &str = "invalid_provider";
pub const KEYRING_ERROR: &str = "keyring_error";

/// Provider names are `^[a-z0-9-]{1,32}$`.
pub fn validate_provider(provider: &str) -> Result<(), String> {
    let ok = (1..=32).contains(&provider.len())
        && provider.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
    if ok {
        Ok(())
    } else {
        Err(INVALID_PROVIDER.into())
    }
}

pub fn user_for(provider: &str) -> String {
    format!("ai-{provider}")
}

/// Error kind without its payload: some variants (BadEncoding) hold the secret's bytes.
fn error_kind(e: &Error) -> &'static str {
    match e {
        Error::PlatformFailure(_) => "platform_failure",
        Error::NoStorageAccess(_) => "no_storage_access",
        Error::NoEntry => "no_entry",
        Error::BadEncoding(_) => "bad_encoding",
        Error::TooLong(..) => "too_long",
        Error::Invalid(..) => "invalid",
        Error::Ambiguous(_) => "ambiguous",
        _ => "other",
    }
}

fn fail(op: &str, provider: &str, e: &Error) -> String {
    eprintln!("[vedit] keyring {op} for provider {provider} failed: {}", error_kind(e));
    KEYRING_ERROR.into()
}

fn entry(provider: &str) -> Result<Entry, String> {
    validate_provider(provider)?;
    Entry::new(SERVICE, &user_for(provider)).map_err(|e| fail("open", provider, &e))
}

pub fn set_key(provider: &str, key: &str) -> Result<(), String> {
    entry(provider)?.set_password(key).map_err(|e| fail("set", provider, &e))
}

pub fn get_key(provider: &str) -> Result<Option<String>, String> {
    match entry(provider)?.get_password() {
        Ok(key) => Ok(Some(key)),
        Err(Error::NoEntry) => Ok(None),
        Err(e) => Err(fail("get", provider, &e)),
    }
}

/// Deleting a key that is not stored is not an error.
pub fn delete_key(provider: &str) -> Result<(), String> {
    match entry(provider)?.delete_credential() {
        Ok(()) | Err(Error::NoEntry) => Ok(()),
        Err(e) => Err(fail("delete", provider, &e)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_valid_providers() {
        for p in ["openai", "anthropic", "a", "open-router-2", &"x".repeat(32)] {
            assert_eq!(validate_provider(p), Ok(()), "{p}");
        }
    }

    #[test]
    fn rejects_invalid_providers() {
        for p in ["", "OpenAI", "a b", "a_b", "a/b", "../x", "é", &"x".repeat(33)] {
            assert_eq!(validate_provider(p), Err(INVALID_PROVIDER.to_string()), "{p:?}");
        }
    }

    #[test]
    fn user_name_is_prefixed() {
        assert_eq!(user_for("openai"), "ai-openai");
    }

    #[test]
    fn invalid_provider_never_reaches_the_store() {
        assert_eq!(set_key("Bad Name", "k"), Err(INVALID_PROVIDER.to_string()));
        assert_eq!(get_key("Bad Name"), Err(INVALID_PROVIDER.to_string()));
        assert_eq!(delete_key("Bad Name"), Err(INVALID_PROVIDER.to_string()));
    }

    /// Needs the real Windows credential store: `cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn keyring_round_trip() {
        let p = "vedit-selftest";
        delete_key(p).unwrap();
        assert_eq!(get_key(p).unwrap(), None);
        set_key(p, "sk-test-value").unwrap();
        assert_eq!(get_key(p).unwrap().as_deref(), Some("sk-test-value"));
        set_key(p, "sk-test-value-2").unwrap();
        assert_eq!(get_key(p).unwrap().as_deref(), Some("sk-test-value-2"));
        delete_key(p).unwrap();
        assert_eq!(get_key(p).unwrap(), None);
        delete_key(p).unwrap();
    }
}
