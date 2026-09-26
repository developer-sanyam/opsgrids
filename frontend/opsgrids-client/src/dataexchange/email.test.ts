import { describe, expect, it } from "vitest";

import { normaliseEmail } from "./email";

describe("normaliseEmail: what it keeps", () => {
  it("trims, keeps the local part as typed and lower-cases the domain", () => {
    expect(normaliseEmail("  Ada.Lovelace@Example.COM  ")).toBe("Ada.Lovelace@example.com");
    expect(normaliseEmail("USER@Mail.EXAMPLE.org")).toBe("USER@mail.example.org");
  });

  it("accepts the punctuation the server accepts in a local part", () => {
    for (const address of ["a+b@example.com", "o'brien@example.com", "a_b-c.d@example.com", "a!#$%&*/=?^`{|}~@example.com"]) {
      expect(normaliseEmail(address), address).toBe(address);
    }
  });

  it("accepts a subdomain, a hyphen inside a label and digits", () => {
    expect(normaliseEmail("a@mail-1.sub.example.co.uk")).toBe("a@mail-1.sub.example.co.uk");
    expect(normaliseEmail("1@2.example.com")).toBe("1@2.example.com");
  });

  it("accepts example as a top-level name: it is not on the reserved list", () => {
    expect(normaliseEmail("a@site.example")).toBe("a@site.example");
    expect(normaliseEmail("a@example.com")).toBe("a@example.com");
  });

  it("accepts non-ASCII letters in either part", () => {
    expect(normaliseEmail("józef@Zażółć.example")).toBe("józef@zażółć.example");
  });

  it("accepts a local part of 64 characters and refuses 65", () => {
    expect(normaliseEmail(`${"a".repeat(64)}@example.com`)).toBe(`${"a".repeat(64)}@example.com`);
    expect(normaliseEmail(`${"a".repeat(65)}@example.com`)).toBeNull();
  });
});

describe("normaliseEmail: what the server refuses", () => {
  it("refuses consecutive dots and a dot at either end of the local part", () => {
    for (const address of ["a..b@example.com", ".a@example.com", "a.@example.com", "a@example..com", "a@.example.com", "a@example.com."]) {
      expect(normaliseEmail(address), address).toBeNull();
    }
  });

  it("refuses a domain label that starts or ends with a hyphen", () => {
    for (const address of ["a@-example.com", "a@example-.com", "a@sub.-x.example.com", "a@x-.example.com"]) {
      expect(normaliseEmail(address), address).toBeNull();
    }
  });

  it("refuses an underscore in the domain, but not in the local part", () => {
    expect(normaliseEmail("a@exa_mple.com")).toBeNull();
    expect(normaliseEmail("a_b@example.com")).toBe("a_b@example.com");
  });

  it("refuses whitespace and commas anywhere", () => {
    for (const address of ["a b@example.com", "a@exa mple.com", "a,b@example.com", "a@example,com", "a@example.com b", "a\tb@example.com"]) {
      expect(normaliseEmail(address), address).toBeNull();
    }
  });

  it("refuses the reserved top-level names, in any case, and their subdomains", () => {
    for (const tld of ["test", "local", "invalid", "localhost", "onion", "arpa"]) {
      expect(normaliseEmail(`a@site.${tld}`), tld).toBeNull();
      expect(normaliseEmail(`a@site.${tld.toUpperCase()}`), tld).toBeNull();
      expect(normaliseEmail(`a@x.y.${tld}`), tld).toBeNull();
    }
  });

  it("refuses a domain with no dot, a numeric top-level name and an address literal", () => {
    expect(normaliseEmail("a@localhost")).toBeNull();
    expect(normaliseEmail("a@example")).toBeNull();
    expect(normaliseEmail("a@example.123")).toBeNull();
    expect(normaliseEmail("a@[192.168.0.1]")).toBeNull();
  });

  it("refuses a missing or repeated at sign, and a quoted or bracketed local part", () => {
    for (const address of ["", "example.com", "@example.com", "a@", "a@@example.com", "a@b@example.com", '"a b"@example.com', "a(b)@example.com", "<a@example.com>", "a:b@example.com"]) {
      expect(normaliseEmail(address), address).toBeNull();
    }
  });

  it("refuses a label longer than 63 characters, and an address longer than 254", () => {
    expect(normaliseEmail(`a@${"x".repeat(63)}.example.com`)).not.toBeNull();
    expect(normaliseEmail(`a@${"x".repeat(64)}.example.com`)).toBeNull();
    const label = "x".repeat(63);
    expect(normaliseEmail(`a@${label}.${label}.${label}.${label}.com`)).toBeNull();
  });
});
