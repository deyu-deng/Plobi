import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { Check, KeyRound, ShieldCheck, Smartphone, Trash2, Users, X } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type {
  DevicePairCode,
  PairingResponse,
  PairingUser,
  PairedDevice,
} from "@/lib/api";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { usePageHeader } from "@/contexts/usePageHeader";

type ShowToast = ReturnType<typeof useToast>["showToast"];

function formatWhen(epochSeconds: number): string {
  if (!epochSeconds) return "never";
  return new Date(epochSeconds * 1000).toLocaleString();
}

function formatCountdown(secondsLeft: number): string {
  const safe = Math.max(0, secondsLeft);
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

/**
 * The handset half of this page (WP-APP-PAIR). Each paired device holds its own
 * token, so the row here is the thing that makes "revoke the lost tablet" mean
 * "end one device" instead of "rotate a secret every device shares".
 */
function DevicePairingSection({ showToast }: { showToast: ShowToast }) {
  const [devices, setDevices] = useState<PairedDevice[]>([]);
  const [code, setCode] = useState<DevicePairCode | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [lockLeft, setLockLeft] = useState(0);
  const [lockedUntil, setLockedUntil] = useState(0);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);

  const loadDevices = useCallback(() => {
    api
      .getPairedDevices()
      .then((res) => {
        setDevices(res.devices);
        setLockedUntil(res.pairingLockedUntil);
        // Redeeming closes the window server-side. The card has to go with it —
        // a spent code still sitting there looks live to whoever is watching.
        setCode((current) => (current && !res.codeExpiresAt ? null : current));
      })
      .catch(() => showToast("Failed to load paired devices", "error"))
      .finally(() => setLoading(false));
  }, [showToast]);

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  // One clock drives both live numbers. Comparing against `Date.now()` while
  // rendering is not pure, and would also leave a lapsed window or a spent
  // lockout on screen until something else happened to re-render.
  useEffect(() => {
    const tick = () => {
      const now = Date.now() / 1000;
      setSecondsLeft(code ? Math.max(0, Math.round(code.expiresAt - now)) : 0);
      setLockLeft(lockedUntil ? Math.max(0, Math.round(lockedUntil - now)) : 0);
      if (code && code.expiresAt <= now) setCode(null);
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [code, lockedUntil]);

  // While a window is open the list is polled, so the handset shows up the
  // moment someone finishes typing the code on it — no reload button to hunt for.
  useEffect(() => {
    if (!code) return;
    const id = window.setInterval(loadDevices, 3000);
    return () => window.clearInterval(id);
  }, [code, loadDevices]);

  const handleStart = async () => {
    setStarting(true);
    try {
      setCode(await api.startDevicePairing());
      loadDevices();
    } catch (e) {
      showToast(`Error: ${e}`, "error");
      loadDevices();
    } finally {
      setStarting(false);
    }
  };

  const deviceRevoke = useConfirmDelete({
    onDelete: useCallback(
      async (deviceId: string) => {
        setRevoking(deviceId);
        try {
          await api.revokePairedDevice(deviceId);
          const gone = devices.find((d) => d.deviceId === deviceId);
          showToast(`Revoked: "${gone?.name ?? deviceId}"`, "success");
          loadDevices();
        } catch (e) {
          showToast(`Error: ${e}`, "error");
          throw e;
        } finally {
          setRevoking(null);
        }
      },
      [devices, loadDevices, showToast],
    ),
  });

  const pendingDevice = deviceRevoke.pendingId
    ? devices.find((d) => d.deviceId === deviceRevoke.pendingId)
    : null;

  return (
    <div className="flex flex-col gap-3">
      <H2
        variant="sm"
        className="flex items-center gap-2 text-muted-foreground"
      >
        <Smartphone className="h-4 w-4" />
        Paired devices ({devices.length})
      </H2>

      <DeleteConfirmDialog
        open={deviceRevoke.isOpen}
        onCancel={deviceRevoke.cancel}
        onConfirm={deviceRevoke.confirm}
        title="Revoke this device"
        description={
          pendingDevice
            ? `"${pendingDevice.name}" will have to pair again to reach the board. Its token stops working on the next request.`
            : "This device will have to pair again to reach the board."
        }
        confirmLabel="Revoke"
        loading={deviceRevoke.isDeleting}
      />

      {lockLeft > 0 && (
        <Card>
          <CardContent className="py-4 text-sm text-destructive">
            Pairing is locked after repeated failed codes. Try again in{" "}
            {formatCountdown(lockLeft)} (at{" "}
            {new Date(lockedUntil * 1000).toLocaleString()}). Devices already
            paired are unaffected.
          </CardContent>
        </Card>
      )}

      {code && (
        <Card>
          <CardContent className="flex flex-col gap-2 py-5">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              Enter this code on the device
            </div>
            <div className="font-mono text-3xl tracking-[0.3em]">
              {code.code}
            </div>
            <div className="text-xs text-muted-foreground">
              {formatCountdown(secondsLeft)} left · one use · a new code
              invalidates this one
            </div>
          </CardContent>
        </Card>
      )}

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          className="uppercase"
          onClick={handleStart}
          disabled={starting || lockLeft > 0}
          prefix={
            starting ? <Spinner /> : <KeyRound className="h-4 w-4" />
          }
        >
          {code ? "New code" : "Pair a device"}
        </Button>
        {loading && <Spinner className="text-sm text-muted-foreground" />}
      </div>

      {!loading && devices.length === 0 && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No paired devices
          </CardContent>
        </Card>
      )}

      {devices.map((device) => (
        <Card key={device.deviceId}>
          <CardContent className="flex items-start gap-4 py-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <Badge tone="outline">device</Badge>
                <span className="font-medium text-sm truncate">
                  {device.name}
                </span>
              </div>
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span className="font-mono truncate">{device.deviceId}</span>
                <span>paired {formatWhen(device.pairedAt)}</span>
                <span>seen {formatWhen(device.lastSeenAt)}</span>
              </div>
            </div>

            <div className="flex items-center gap-1 shrink-0">
              {revoking === device.deviceId ? (
                <Spinner className="text-sm" />
              ) : (
                <Button
                  ghost
                  size="icon"
                  title="Revoke"
                  aria-label={`Revoke ${device.name}`}
                  className="text-destructive"
                  onClick={() => deviceRevoke.requestDelete(device.deviceId)}
                >
                  <X />
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function getUserKey(user: PairingUser): string {
  return `${user.platform}:${user.user_id}`;
}

function splitUserKey(key: string): { platform: string; user_id: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { platform: "", user_id: key };
  return { platform: key.slice(0, idx), user_id: key.slice(idx + 1) };
}

function getUserLabel(user: PairingUser): string {
  return user.user_name || user.user_id;
}

export default function PairingPage() {
  const [pending, setPending] = useState<PairingUser[]>([]);
  const [approved, setApproved] = useState<PairingUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [approving, setApproving] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const { toast, showToast } = useToast();
  const { setEnd } = usePageHeader();

  const loadPairing = useCallback(() => {
    api
      .getPairing()
      .then((res: PairingResponse) => {
        setPending(res.pending);
        setApproved(res.approved);
      })
      .catch(() => showToast("Failed to load pairing requests", "error"))
      .finally(() => setLoading(false));
  }, [showToast]);

  useEffect(() => {
    loadPairing();
  }, [loadPairing]);

  const handleApprove = async (user: PairingUser) => {
    if (!user.code) {
      showToast("Missing pairing code", "error");
      return;
    }
    const key = getUserKey(user);
    setApproving(key);
    try {
      await api.approvePairing(user.platform, user.code);
      showToast(`Approved: "${getUserLabel(user)}"`, "success");
      loadPairing();
    } catch (e) {
      showToast(`Error: ${e}`, "error");
    } finally {
      setApproving(null);
    }
  };

  const handleClearPending = async () => {
    if (!window.confirm("Clear all pending pairing requests?")) return;
    setClearing(true);
    try {
      const res = await api.clearPendingPairing();
      showToast(`Cleared ${res.cleared} pending request(s)`, "success");
      loadPairing();
    } catch (e) {
      showToast(`Error: ${e}`, "error");
    } finally {
      setClearing(false);
    }
  };

  const userRevoke = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const { platform, user_id } = splitUserKey(key);
        const user = approved.find((u) => getUserKey(u) === key);
        try {
          await api.revokePairing(platform, user_id);
          showToast(
            `Revoked: "${user ? getUserLabel(user) : user_id}"`,
            "success",
          );
          loadPairing();
        } catch (e) {
          showToast(`Error: ${e}`, "error");
          throw e;
        }
      },
      [approved, loadPairing, showToast],
    ),
  });

  // Put "Clear pending" button in page header
  useLayoutEffect(() => {
    setEnd(
      <Button
        className="uppercase"
        size="sm"
        onClick={handleClearPending}
        disabled={clearing}
        prefix={clearing ? <Spinner /> : <Trash2 className="h-4 w-4" />}
      >
        Clear pending
      </Button>,
    );
    return () => {
      setEnd(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setEnd, clearing]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const pendingRevokeUser = userRevoke.pendingId
    ? approved.find((u) => getUserKey(u) === userRevoke.pendingId)
    : null;

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />

      <DevicePairingSection showToast={showToast} />

      <DeleteConfirmDialog
        open={userRevoke.isOpen}
        onCancel={userRevoke.cancel}
        onConfirm={userRevoke.confirm}
        title="Revoke access"
        description={
          pendingRevokeUser
            ? `"${getUserLabel(pendingRevokeUser)}" will lose access. This cannot be undone.`
            : "This user will lose access. This cannot be undone."
        }
        confirmLabel="Revoke"
        loading={userRevoke.isDeleting}
      />

      {/* Pending requests */}
      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <Users className="h-4 w-4" />
          Pending requests ({pending.length})
        </H2>

        {pending.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No pending pairing requests
            </CardContent>
          </Card>
        )}

        {pending.map((user) => {
          const key = getUserKey(user);
          return (
            <Card key={key}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge tone="outline">{user.platform}</Badge>
                    {user.code && (
                      <span className="font-mono text-sm">{user.code}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span className="truncate">{user.user_id}</span>
                    {user.user_name && (
                      <span className="truncate">{user.user_name}</span>
                    )}
                    {typeof user.age_minutes === "number" && (
                      <span>{user.age_minutes}m ago</span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    size="sm"
                    className="uppercase"
                    onClick={() => handleApprove(user)}
                    disabled={approving === key || !user.code}
                    prefix={
                      approving === key ? (
                        <Spinner />
                      ) : (
                        <Check className="h-4 w-4" />
                      )
                    }
                  >
                    Approve
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Approved users */}
      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <ShieldCheck className="h-4 w-4" />
          Approved users ({approved.length})
        </H2>

        {approved.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No approved users
            </CardContent>
          </Card>
        )}

        {approved.map((user) => {
          const key = getUserKey(user);
          return (
            <Card key={key}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge tone="outline">{user.platform}</Badge>
                    <span className="font-medium text-sm truncate">
                      {user.user_id}
                    </span>
                  </div>
                  {user.user_name && (
                    <div className="text-xs text-muted-foreground truncate">
                      {user.user_name}
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    ghost
                    size="icon"
                    title="Revoke"
                    aria-label="Revoke"
                    className="text-destructive"
                    onClick={() => userRevoke.requestDelete(key)}
                  >
                    <X />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
