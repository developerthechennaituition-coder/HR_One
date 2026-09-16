import { useEffect, useState } from 'react'

const API_URL = 'http://127.0.0.1:8000/api/companies'

const emptyCompany = {
	company: '',
	legalEntity: '',
	branch: '',
	location: '',
}

const companyFields = [
	['company', 'Company'],
	['legalEntity', 'Legal Entity'],
	['branch', 'Branch'],
	['location', 'Location'],
]

function CompanySetup() {
	const [companies, setCompanies] = useState([])
	const [searchTerm, setSearchTerm] = useState('')
	const [formValues, setFormValues] = useState(emptyCompany)
	const [isFormOpen, setIsFormOpen] = useState(false)
		const [editingId, setEditingId] = useState(null)
		const [isLoading, setIsLoading] = useState(true)
		const [isSaving, setIsSaving] = useState(false)
	const [feedback, setFeedback] = useState({ type: '', message: '' })

		const setError = (message) => setFeedback({ type: 'danger', message })

		const readError = async (response) => {
			try {
				const error = await response.json()
				return error.detail || 'The request could not be completed.'
			} catch {
				return 'The request could not be completed.'
			}
		}

		const toFormCompany = (company) => ({
			company: company.company,
			legalEntity: company.legal_entity,
			branch: company.branch,
			location: company.location,
		})

		useEffect(() => {
			const loadCompanies = async () => {
				try {
					const response = await fetch(API_URL)
					if (!response.ok) {
						throw new Error(await readError(response))
					}

					const data = await response.json()
					setCompanies(data.map((company) => ({ ...company, ...toFormCompany(company) })))
				} catch (error) {
					setError(error.message || 'Unable to load companies.')
				} finally {
					setIsLoading(false)
				}
			}

			loadCompanies()
		}, [])

	useEffect(() => {
		if (!feedback.message) {
			return undefined
		}

		const feedbackTimer = window.setTimeout(() => {
			setFeedback({ type: '', message: '' })
		}, 3000)

		return () => window.clearTimeout(feedbackTimer)
	}, [feedback.message])

	const filteredCompanies = companies.filter((company) =>
		company.company.toLowerCase().includes(searchTerm.toLowerCase()),
	)

	const openAddForm = () => {
		setEditingId(null)
		setFormValues(emptyCompany)
		setFeedback({ type: '', message: '' })
		setIsFormOpen(true)
	}

	const openEditForm = (company) => {
		setEditingId(company.id)
		setFormValues(toFormCompany(company))
		setFeedback({ type: '', message: '' })
		setIsFormOpen(true)
	}

	const closeForm = () => {
		setEditingId(null)
		setFormValues(emptyCompany)
		setFeedback({ type: '', message: '' })
		setIsFormOpen(false)
	}

	const handleChange = (event) => {
		const { name, value } = event.target
		setFormValues((currentValues) => ({ ...currentValues, [name]: value }))
		setFeedback({ type: '', message: '' })
	}

	const handleSubmit = async (event) => {
		event.preventDefault()

		const hasEmptyField = companyFields.some(([name]) => !formValues[name].trim())
		if (hasEmptyField) {
			setFeedback({ type: 'danger', message: 'Please enter all company details.' })
			return
		}

		setIsSaving(true)
		const payload = {
			company: formValues.company.trim(),
			legal_entity: formValues.legalEntity.trim(),
			branch: formValues.branch.trim(),
			location: formValues.location.trim(),
		}

		try {
			const response = await fetch(editingId ? `${API_URL}/${editingId}` : API_URL, {
				method: editingId ? 'PUT' : 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload),
			})
			if (!response.ok) {
				throw new Error(await readError(response))
			}

			const savedCompany = await response.json()
			const companyForState = { ...savedCompany, ...toFormCompany(savedCompany) }
			setCompanies((currentCompanies) =>
				editingId
					? currentCompanies.map((company) =>
							company.id === editingId ? companyForState : company,
						)
					: [...currentCompanies, companyForState],
			)
			setFeedback({
				type: 'success',
				message: `${payload.company} company entity ${editingId ? 'updated' : 'saved'} successfully.`,
			})
			setEditingId(null)
			setFormValues(emptyCompany)
			setIsFormOpen(false)
		} catch (error) {
			setError(error.message || 'Unable to save company.')
		} finally {
			setIsSaving(false)
		}
	}

	const handleDeactivate = async (companyToDeactivate) => {
		const shouldDeactivate = window.confirm(
			`Are you sure you want to deactivate ${companyToDeactivate.company}? It will be hidden from active companies, not permanently deleted.`,
		)

		if (!shouldDeactivate) {
			return
		}

		try {
			const response = await fetch(`${API_URL}/${companyToDeactivate.id}`, { method: 'DELETE' })
			if (!response.ok) {
				throw new Error(await readError(response))
			}

			setCompanies((currentCompanies) =>
				currentCompanies.filter((company) => company.id !== companyToDeactivate.id),
			)
			setEditingId(null)
			setFormValues(emptyCompany)
			setIsFormOpen(false)
			setFeedback({
				type: 'success',
				message: `${companyToDeactivate.company} company entity deactivated successfully.`,
			})
		} catch (error) {
			setError(error.message || 'Unable to deactivate company.')
		}
	}

	return (
		<section className="company-setup-page">
			<div className="company-setup-toolbar d-flex flex-wrap justify-content-between align-items-center gap-3 mb-4">
				<h2 className="mb-0">Company / Legal Entity Setup</h2>
				<button type="button" className="btn btn-primary company-add-button" onClick={openAddForm}>
					+ Add Company
				</button>
			</div>

			{feedback.message && (
				<div className={`alert alert-${feedback.type}`} role="alert" aria-live="polite">
					{feedback.message}
				</div>
			)}

			{isFormOpen && (
				<div className="card border-0 shadow-sm mb-4">
					<div className="card-body p-4">
						<h5 className="card-title mb-4">
							{editingId === null ? 'Add Company' : 'Edit Company'}
						</h5>

						<form onSubmit={handleSubmit}>
							<div className="row g-3">
								{companyFields.map(([name, label]) => (
									<div className="col-md-6" key={name}>
										<label htmlFor={name} className="form-label">
											{label}
										</label>
										<input
											id={name}
											name={name}
											type="text"
											className="form-control"
											value={formValues[name]}
											onChange={handleChange}
										/>
									</div>
								))}
							</div>

							<div className="company-form-actions d-flex justify-content-end gap-2 mt-4">
								<button type="button" className="btn btn-outline-secondary" onClick={closeForm}>
									Cancel
								</button>
								<button type="submit" className="btn btn-primary" disabled={isSaving}>
									{isSaving ? 'Saving...' : 'Save'}
								</button>
							</div>
						</form>
					</div>
				</div>
			)}

			<div className="card border-0 shadow-sm">
				<div className="card-body">
					<div className="mb-4">
						<label htmlFor="company-search" className="visually-hidden">
							Search company
						</label>
						<input
							id="company-search"
							type="search"
							className="form-control"
							placeholder="Search company..."
							value={searchTerm}
							onChange={(event) => setSearchTerm(event.target.value)}
						/>
					</div>

					<div className="company-table-wrapper table-responsive">
						<table className="company-table table table-hover align-middle mb-0">
							<thead>
								<tr>
									<th scope="col">Company</th>
									<th scope="col">Legal Entity</th>
									<th scope="col">Branch</th>
									<th scope="col">Location</th>
									<th scope="col">Action</th>
								</tr>
							</thead>
							<tbody>
								{isLoading ? (
									<tr>
										<td colSpan="5" className="text-center text-muted py-4">
											Loading companies...
										</td>
									</tr>
								) : filteredCompanies.length === 0 ? (
									<tr>
										<td colSpan="5" className="text-center text-muted py-4">
											No companies available
										</td>
									</tr>
								) : (
									filteredCompanies.map((company) => {
										return (
											<tr key={company.id}>
												<td>{company.company}</td>
												<td>{company.legalEntity}</td>
												<td>{company.branch}</td>
												<td>{company.location}</td>
												<td className="text-nowrap">
													<button
														type="button"
														className="btn btn-sm btn-outline-primary me-2"
															onClick={() => openEditForm(company)}
													>
														Edit
													</button>
													<button
														type="button"
														className="btn btn-sm btn-outline-danger"
														onClick={() => handleDeactivate(company)}
													>
														Deactivate
													</button>
												</td>
											</tr>
										)
									})
								)}
							</tbody>
						</table>
					</div>
				</div>
			</div>
		</section>
	)
}

export default CompanySetup
