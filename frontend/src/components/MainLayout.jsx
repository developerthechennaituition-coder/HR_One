import Sidebar from './Sidebar'
import Header from './Header'

function MainLayout() {
  return (
    <div className="hrms-layout">

      <Sidebar />

      <div className="main-area">

        <Header />

        <main className="main-content">
          <h2>Welcome to HRMS</h2>

          <p>
            Human Resource Management System
          </p>

          <div className="row g-4 mt-2">

            <div className="col-md-4">
              <div className="card dashboard-card">
                <div className="card-body">
                  <h6>Total Employees</h6>
                  <h3>0</h3>
                </div>
              </div>
            </div>

            <div className="col-md-4">
              <div className="card dashboard-card">
                <div className="card-body">
                  <h6>Present Today</h6>
                  <h3>0</h3>
                </div>
              </div>
            </div>

            <div className="col-md-4">
              <div className="card dashboard-card">
                <div className="card-body">
                  <h6>Leave Requests</h6>
                  <h3>0</h3>
                </div>
              </div>
            </div>

          </div>
        </main>

      </div>

    </div>
  )
}

export default MainLayout