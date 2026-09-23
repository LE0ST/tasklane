import { test, expect } from '@playwright/test';

test.describe('TaskLane Kanban Board E2E Tests', () => {
  let consoleErrors = [];
  let pageErrors = [];

  test.beforeEach(async ({ page }) => {
    consoleErrors = [];
    pageErrors = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });

    page.on('pageerror', (exception) => {
      pageErrors.push(exception.message);
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');
  });

  test.afterEach(async () => {
    // Assert no uncaught JavaScript exceptions occurred on any page
    expect(pageErrors).toEqual([]);
  });

  // Scenario 1: Application loads successfully
  test('1. Application loads successfully and displays header branding', async ({ page }) => {
    await expect(page).toHaveTitle(/TaskLane/i);
    const headerTitle = page.locator('.brand-section h1');
    await expect(headerTitle).toHaveText('TaskLane');
    const headerSubtitle = page.locator('.brand-section p');
    await expect(headerSubtitle).toContainText('FastAPI Connected');
  });

  // Scenario 2: All five Kanban columns are rendered
  test('2. All five Kanban columns are rendered (Backlog, To Do, In Progress, Review, Done)', async ({ page }) => {
    const expectedColumns = ['Backlog', 'To Do', 'In Progress', 'Review', 'Done'];
    const columnTitles = page.locator('.kanban-column .column-title');
    await expect(columnTitles).toHaveCount(5);
    await expect(columnTitles).toHaveText(expectedColumns);
  });

  // Scenario 3: Create a new task in Backlog
  test('3. Create a new task in Backlog', async ({ page }) => {
    const timestamp = Date.now();
    const taskTitle = `E2E Create Task ${timestamp}`;
    const taskDesc = `Description for test ${timestamp}`;

    // Click "+ New Task"
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await expect(page.locator('.modal-content')).toBeVisible();

    // Fill form
    await page.locator('input[placeholder="Task title (required)"]').fill(taskTitle);
    await page.locator('textarea[placeholder="Optional details or context"]').fill(taskDesc);
    await page.locator('.modal-content select').selectOption('high');

    // Submit
    await page.locator('.modal-content button[type="submit"]').click();

    // Verify modal is closed
    await expect(page.locator('.modal-content')).not.toBeVisible();

    // Verify task is in Backlog column
    const backlogColumn = page.locator('.kanban-column').filter({ has: page.locator('.column-title', { hasText: 'Backlog' }) });
    const taskCard = backlogColumn.locator('.task-card', { hasText: taskTitle });
    await expect(taskCard).toBeVisible();
    await expect(taskCard.locator('.task-title')).toHaveText(taskTitle);
    await expect(taskCard.locator('.task-description')).toHaveText(taskDesc);
    await expect(taskCard.locator('.priority-badge')).toHaveText('high');

    // Cleanup: delete the task
    page.once('dialog', (dialog) => dialog.accept());
    await taskCard.locator('button[title="Delete task"]').click();
    await expect(taskCard).not.toBeVisible();
  });

  // Scenario 4: Move a task forward through columns and backward
  test('4. Move a task forward through columns and backward', async ({ page }) => {
    const timestamp = Date.now();
    const taskTitle = `E2E Move Task ${timestamp}`;

    // Create task
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(taskTitle);
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    const getColumn = (colName) =>
      page.locator('.kanban-column').filter({ has: page.locator('.column-title', { hasText: colName }) });

    // Step 1: Initial state in Backlog
    let backlogCard = getColumn('Backlog').locator('.task-card', { hasText: taskTitle });
    await expect(backlogCard).toBeVisible();
    await expect(backlogCard.locator('button[title="Move left"]')).toBeDisabled();
    await expect(backlogCard.locator('button[title="Move right"]')).toBeEnabled();

    // Step 2: Move Backlog -> To Do
    await backlogCard.locator('button[title="Move right"]').click();
    const todoCard = getColumn('To Do').locator('.task-card', { hasText: taskTitle });
    await expect(todoCard).toBeVisible();
    await expect(backlogCard).not.toBeVisible();

    // Step 3: Move To Do -> In Progress
    await todoCard.locator('button[title="Move right"]').click();
    const inProgressCard = getColumn('In Progress').locator('.task-card', { hasText: taskTitle });
    await expect(inProgressCard).toBeVisible();

    // Step 4: Move In Progress -> Review
    await inProgressCard.locator('button[title="Move right"]').click();
    const reviewCard = getColumn('Review').locator('.task-card', { hasText: taskTitle });
    await expect(reviewCard).toBeVisible();

    // Step 5: Move Review -> Done
    await reviewCard.locator('button[title="Move right"]').click();
    const doneCard = getColumn('Done').locator('.task-card', { hasText: taskTitle });
    await expect(doneCard).toBeVisible();
    await expect(doneCard.locator('button[title="Move right"]')).toBeDisabled();
    await expect(doneCard.locator('button[title="Move left"]')).toBeEnabled();

    // Step 6: Move backward Done -> Review
    await doneCard.locator('button[title="Move left"]').click();
    await expect(reviewCard).toBeVisible();
    await expect(doneCard).not.toBeVisible();

    // Cleanup: delete task
    page.once('dialog', (dialog) => dialog.accept());
    await reviewCard.locator('button[title="Delete task"]').click();
    await expect(reviewCard).not.toBeVisible();
  });

  // Scenario 5: Filter tasks by priority
  test('5. Filter tasks by priority', async ({ page }) => {
    const timestamp = Date.now();
    const urgentTitle = `E2E Urgent ${timestamp}`;
    const lowTitle = `E2E Low ${timestamp}`;

    // Create Urgent task
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(urgentTitle);
    await page.locator('.modal-content select').selectOption('urgent');
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    // Create Low task
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(lowTitle);
    await page.locator('.modal-content select').selectOption('low');
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    // Both should be visible with "All Priorities"
    await expect(page.locator('.task-card', { hasText: urgentTitle })).toBeVisible();
    await expect(page.locator('.task-card', { hasText: lowTitle })).toBeVisible();

    // Filter by Urgent
    await page.locator('select.priority-select').selectOption('urgent');
    await expect(page.locator('.task-card', { hasText: urgentTitle })).toBeVisible();
    await expect(page.locator('.task-card', { hasText: lowTitle })).not.toBeVisible();

    // Filter by Low
    await page.locator('select.priority-select').selectOption('low');
    await expect(page.locator('.task-card', { hasText: urgentTitle })).not.toBeVisible();
    await expect(page.locator('.task-card', { hasText: lowTitle })).toBeVisible();

    // Reset filter
    await page.locator('select.priority-select').selectOption('');
    await expect(page.locator('.task-card', { hasText: urgentTitle })).toBeVisible();
    await expect(page.locator('.task-card', { hasText: lowTitle })).toBeVisible();

    // Cleanup both tasks
    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('.task-card', { hasText: urgentTitle }).locator('button[title="Delete task"]').click();
    await expect(page.locator('.task-card', { hasText: urgentTitle })).not.toBeVisible();

    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('.task-card', { hasText: lowTitle }).locator('button[title="Delete task"]').click();
    await expect(page.locator('.task-card', { hasText: lowTitle })).not.toBeVisible();
  });

  // Scenario 6: Search tasks by title
  test('6. Search tasks by title', async ({ page }) => {
    const timestamp = Date.now();
    const uniqueAlpha = `AlphaSearch-${timestamp}`;
    const uniqueBeta = `BetaSearch-${timestamp}`;

    // Create Alpha task
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(uniqueAlpha);
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    // Create Beta task
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(uniqueBeta);
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    // Search for Alpha
    await page.locator('input.search-input').fill(uniqueAlpha);
    await expect(page.locator('.task-card', { hasText: uniqueAlpha })).toBeVisible();
    await expect(page.locator('.task-card', { hasText: uniqueBeta })).not.toBeVisible();

    // Search for Beta
    await page.locator('input.search-input').fill(uniqueBeta);
    await expect(page.locator('.task-card', { hasText: uniqueAlpha })).not.toBeVisible();
    await expect(page.locator('.task-card', { hasText: uniqueBeta })).toBeVisible();

    // Clear search
    await page.locator('input.search-input').fill('');
    await expect(page.locator('.task-card', { hasText: uniqueAlpha })).toBeVisible();
    await expect(page.locator('.task-card', { hasText: uniqueBeta })).toBeVisible();

    // Cleanup
    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('.task-card', { hasText: uniqueAlpha }).locator('button[title="Delete task"]').click();
    await expect(page.locator('.task-card', { hasText: uniqueAlpha })).not.toBeVisible();

    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('.task-card', { hasText: uniqueBeta }).locator('button[title="Delete task"]').click();
    await expect(page.locator('.task-card', { hasText: uniqueBeta })).not.toBeVisible();
  });

  // Scenario 7: Verify persistence across page reload
  test('7. Verify persistence across page reload', async ({ page }) => {
    const timestamp = Date.now();
    const taskTitle = `E2E Persist Task ${timestamp}`;
    const taskDesc = `Persist Desc ${timestamp}`;

    // Create task
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(taskTitle);
    await page.locator('textarea[placeholder="Optional details or context"]').fill(taskDesc);
    await page.locator('.modal-content select').selectOption('high');
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    // Move to "To Do"
    const backlogColumn = page.locator('.kanban-column').filter({ has: page.locator('.column-title', { hasText: 'Backlog' }) });
    const taskCard = backlogColumn.locator('.task-card', { hasText: taskTitle });
    await taskCard.locator('button[title="Move right"]').click();

    const todoColumn = page.locator('.kanban-column').filter({ has: page.locator('.column-title', { hasText: 'To Do' }) });
    await expect(todoColumn.locator('.task-card', { hasText: taskTitle })).toBeVisible();

    // Reload the page
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Verify task is still in "To Do" column after reload
    const reloadedTodo = page.locator('.kanban-column').filter({ has: page.locator('.column-title', { hasText: 'To Do' }) });
    const reloadedCard = reloadedTodo.locator('.task-card', { hasText: taskTitle });
    await expect(reloadedCard).toBeVisible();
    await expect(reloadedCard.locator('.task-description')).toHaveText(taskDesc);
    await expect(reloadedCard.locator('.priority-badge')).toHaveText('high');

    // Cleanup
    page.once('dialog', (dialog) => dialog.accept());
    await reloadedCard.locator('button[title="Delete task"]').click();
    await expect(reloadedCard).not.toBeVisible();
  });

  // Scenario 8: Delete a task
  test('8. Delete a task removes it permanently from the board', async ({ page }) => {
    const timestamp = Date.now();
    const taskTitle = `E2E Delete Task ${timestamp}`;

    // Create task
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(taskTitle);
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    const card = page.locator('.task-card', { hasText: taskTitle });
    await expect(card).toBeVisible();

    // Accept delete dialog
    page.once('dialog', async (dialog) => {
      expect(dialog.message()).toContain('Are you sure');
      await dialog.accept();
    });

    await card.locator('button[title="Delete task"]').click();
    await expect(card).not.toBeVisible();

    // Reload to verify it was deleted in database too
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.locator('.task-card', { hasText: taskTitle })).not.toBeVisible();
  });

  // Scenario 9: Verify no console errors or uncaught exceptions during basic operations
  test('9. Verify no console errors or uncaught exceptions during basic operations', async ({ page }) => {
    const timestamp = Date.now();
    const taskTitle = `E2E Health Task ${timestamp}`;

    // Perform an end-to-end cycle: open modal, create, move, search, filter, delete
    await page.locator('button.btn-primary', { hasText: '+ New Task' }).click();
    await page.locator('input[placeholder="Task title (required)"]').fill(taskTitle);
    await page.locator('.modal-content select').selectOption('medium');
    await page.locator('.modal-content button[type="submit"]').click();
    await expect(page.locator('.modal-content')).not.toBeVisible();

    const card = page.locator('.task-card', { hasText: taskTitle });
    await expect(card).toBeVisible();

    // Move forward
    await card.locator('button[title="Move right"]').click();

    // Search
    await page.locator('input.search-input').fill(taskTitle);
    await expect(card).toBeVisible();
    await page.locator('input.search-input').fill('');

    // Filter
    await page.locator('select.priority-select').selectOption('medium');
    await expect(card).toBeVisible();
    await page.locator('select.priority-select').selectOption('');

    // Delete
    page.once('dialog', (dialog) => dialog.accept());
    await card.locator('button[title="Delete task"]').click();
    await expect(card).not.toBeVisible();

    // Assert zero console errors and zero page errors
    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
  });
});
