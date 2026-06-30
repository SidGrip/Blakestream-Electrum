import { useState } from 'react'
import { useStore } from '../store'
import { pickBackupOpenPath, relaunchApp, restoreWalletBackup } from '../api'
import SeedInput from './SeedInput'
import './setup.css'

type Mode = 'create' | 'restore' | 'backup'

const WORD_COUNTS = [12, 15, 18, 21, 24]

export default function Setup() {
  const createWallet = useStore((s) => s.createWallet)
  const restoreWallet = useStore((s) => s.restoreWallet)
  const finishOnboarding = useStore((s) => s.finishOnboarding)
  const refresh = useStore((s) => s.refresh)
  const setup = useStore((s) => s.setup)

  const [mode, setMode] = useState<Mode>('create')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [seedWords, setSeedWords] = useState<string[]>([])
  const [backupPath, setBackupPath] = useState('')
  const [restoreDone, setRestoreDone] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newMnemonic, setNewMnemonic] = useState<string | null>(null)
  // In-flight create/restore: backend sets vault_exists before provisioning finishes, so
  // a mid-create poll would flash the "Opening…" card — suppress it until done.
  const [enrolling, setEnrolling] = useState(false)

  const openWallet = async () => {
    // Unmount Setup synchronously before refresh(); else refresh sets provisioned=true
    // while onboarded is still false, briefly re-rendering Create/Restore.
    finishOnboarding()
    await refresh()
  }

  const onCreate = async () => {
    setError(null)
    if (password.length < 8) return setError('Use a password of at least 8 characters.')
    if (password !== confirm) return setError('Passwords do not match.')
    setBusy(true)
    setEnrolling(true)
    try {
      setNewMnemonic(await createWallet(password))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setEnrolling(false)
    } finally {
      setBusy(false)
    }
  }

  const onRestore = async () => {
    setError(null)
    if (!WORD_COUNTS.includes(seedWords.length)) {
      return setError('Enter a 12 / 15 / 18 / 21 / 24-word recovery phrase.')
    }
    if (password.length < 8) return setError('Use a password of at least 8 characters.')
    setBusy(true)
    setEnrolling(true)
    try {
      await restoreWallet(password, seedWords.join(' '))
      await openWallet()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setEnrolling(false)
    } finally {
      setBusy(false)
    }
  }

  const chooseBackup = async () => {
    setError(null)
    const path = await pickBackupOpenPath()
    if (path) setBackupPath(path)
  }

  const onRestoreBackup = async () => {
    setError(null)
    if (!backupPath) return setError('Choose a .bswallet backup file.')
    if (!password) return setError('Enter the wallet password used when this backup was created.')
    setBusy(true)
    setEnrolling(true)
    try {
      await restoreWalletBackup(password, backupPath)
      setRestoreDone(true)
      const relaunched = await relaunchApp()
      if (!relaunched) setError('Backup restored. Close and reopen Blakestream Wallet to load it.')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setEnrolling(false)
    } finally {
      setBusy(false)
    }
  }

  if (newMnemonic) {
    return (
      <div className="setup-shell">
        <div className="setup-card">
          <h1 className="setup-title">BLAKESTREAM WALLET</h1>
          <h2 className="setup-h2">Back up your recovery phrase</h2>
          <p className="setup-muted">
            Write these words down in order and keep them safe — they restore all six coins,
            and will not be shown again.
          </p>
          <div className="seed-grid">
            {newMnemonic.split(' ').map((w, i) => (
              <span key={i} className="seed-word">
                <b>{i + 1}.</b> {w}
              </span>
            ))}
          </div>
          <button className="setup-btn" onClick={openWallet}>
            I&rsquo;ve saved it — Open wallet
          </button>
        </div>
      </div>
    )
  }

  // An existing vault must never fall through to Create/Restore; show a transient "opening"
  // card. Skip while enrolling so a fresh create stays on its Create→phrase flow.
  if (setup?.vault_exists && !enrolling) {
    return (
      <div className="setup-shell">
        <div className="setup-card">
          <h1 className="setup-title">BLAKESTREAM WALLET</h1>
          <h2 className="setup-h2">Opening your wallet…</h2>
          <p className="setup-muted">One moment.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="setup-shell">
      <div className="setup-card">
        <h1 className="setup-title">BLAKESTREAM WALLET</h1>
        <div className="setup-tabs">
          <button className={mode === 'create' ? 'active' : ''} onClick={() => setMode('create')}>
            Create new wallet
          </button>
          <button className={mode === 'restore' ? 'active' : ''} onClick={() => setMode('restore')}>
            Restore from seed
          </button>
          <button className={mode === 'backup' ? 'active' : ''} onClick={() => setMode('backup')}>
            Restore from backup file
          </button>
        </div>
        <p className="setup-muted">One key for all six coins — BLC, BBTC, ELT, LIT, PHO, UMO.</p>

        {mode === 'restore' && (
          <SeedInput onChange={(words) => setSeedWords(words)} />
        )}

        {mode === 'backup' && (
          <div className="setup-backup-box">
            <p>
              Restore a full Blakestream Wallet backup, including the encrypted vault, per-coin wallet files,
              contacts, settings, and Lightning channel state saved in those wallet files.
            </p>
            <p>
              Enter the wallet password that was active when the backup was created. That same password
              encrypts the backup file and unlocks the restored wallet.
            </p>
            <button type="button" className="setup-secondary-btn" onClick={chooseBackup} disabled={busy}>
              Choose backup file
            </button>
            {backupPath && <div className="setup-path" title={backupPath}>{backupPath}</div>}
          </div>
        )}

        <input
          className="setup-input"
          type="password"
          placeholder={mode === 'backup' ? 'Backup wallet password' : 'Wallet password'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="off"
        />
        {mode === 'create' && (
          <input
            className="setup-input"
            type="password"
            placeholder="Confirm password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="off"
          />
        )}

        {error && <div className="setup-error">{error}</div>}
        {restoreDone && <div className="setup-success">Backup restored. Restarting wallet…</div>}

        <button
          className="setup-btn"
          disabled={busy}
          onClick={mode === 'create' ? onCreate : mode === 'restore' ? onRestore : onRestoreBackup}
        >
          {busy ? (
            <>
              <span className="setup-spinner" />
              Working…
            </>
          ) : mode === 'create' ? (
            'Create wallet'
          ) : mode === 'backup' ? (
            'Restore backup file'
          ) : (
            'Restore wallet'
          )}
        </button>
      </div>
    </div>
  )
}
