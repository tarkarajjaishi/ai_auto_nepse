"use client";

import { Pending } from "@/app/admin/floorsheet/page";

export default function AccountPage() {
  return (
    <Pending
      title="Account"
      note="NAASA holdings, order book and collateral. Deliberately last: these are the only authenticated, account-scoped calls in the system, and the order path can move real money — it needs Auth.js in front of it before it is exposed on a web surface, not after."
    />
  );
}
