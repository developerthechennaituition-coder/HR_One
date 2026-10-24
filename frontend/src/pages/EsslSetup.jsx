import EsslAdminSettings from "../components/EsslAdminSettings";

// ESSL biometric device setup, synchronisation and issues (HR Admin only).
function EsslSetup({ session }) {
  return (
    <div className="container-fluid">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="mb-1">ESSL Attendance Setup</h2>
          <p className="text-muted mb-0">
            Connect the ESSL biometric device, synchronise punches and review sync issues
          </p>
        </div>
      </div>

      <EsslAdminSettings session={session} />
    </div>
  );
}

export default EsslSetup;
